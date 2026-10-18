import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .config import settings
from .db import init_db, get_db
from .models import Scan
from .scanner import VALID_SECTORS, MarketDataUnavailable, scan_crypto, scan_by_cap
from .schemas import (
    ScanOut, ScanSummaryOut, RunScanRequest, TechnicalScanOut, CandlesOut,
    EmaBreakoutScanOut, EmaTouch4hScanOut, EmaDoubleTouchForexScanOut, PatternScanOut, TripleTouchScanOut,
    QuoteOut, StockProfileOut, MarketCandidateOut, CryptoScanOut, CapScanOut, ChartAnalysisOut,
    ChatReplyOut, NewsDigestIn, NewsDigestOut, NewsItemOut, WorldNewsOut,
    SymbolSearchOut, SymbolMatchOut, BillingStatusOut,
    DailyDigestIn, DailyDigestOut,
    GoogleAuthIn, AuthResultOut, UserOut,
)
from .pipeline import run_scan
from .scheduler import start_scheduler, next_run_times
from .technical_scan import (
    scan_technical_signals, DEFAULT_TIMEFRAME, scan_ema50_breakout, scan_ema50_touch_4h,
    scan_ema50_double_touch_forex_1h, scan_pattern_strategy, scan_triple_touch_1h,
)
from .candles import get_candles, DEFAULT_CANDLE_TIMEFRAME
from .quote import get_quote, search_symbols
from .chart_analysis import analyze_chart_image
from .chat import generate_chat_reply
from .ai_summary import (
    generate_news_digest, generate_world_news_hebrew, generate_daily_digest,
    generate_outlook, generate_company_blurb,
)
from .enrichment import enrich_symbol
from .world_news import get_world_news
from . import license as license_module
from . import paddle as paddle_module
from . import auth as auth_module

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield


app = FastAPI(title="Nasdaq Extreme Movers Bot", lifespan=lifespan)


@app.exception_handler(MarketDataUnavailable)
async def market_data_unavailable_handler(request, exc):
    return JSONResponse(
        status_code=503,
        content={"detail": "לא ניתן לקבל כרגע נתוני מניות עבור הסינון הזה מספקי הנתונים. הסריקה נכשלה ולא נשמרה. נסה שוב מאוחר יותר."},
    )

_technical_scan_lock = threading.Lock()
_last_technical_scan: dict[str, TechnicalScanOut] = {}

_ema_breakout_scan_lock = threading.Lock()
_last_ema_breakout_scan: Optional[EmaBreakoutScanOut] = None

_ema_touch_4h_scan_lock = threading.Lock()
_last_ema_touch_4h_scan: Optional[EmaTouch4hScanOut] = None

_ema_double_touch_forex_scan_lock = threading.Lock()
_last_ema_double_touch_forex_scan: Optional[EmaDoubleTouchForexScanOut] = None

_pattern_strategy_scan_lock = threading.Lock()
_last_pattern_strategy_scan: Optional[PatternScanOut] = None

# Each profile costs two OpenAI calls, so reopening the same stock's modal reuses
# the last one for a while instead of regenerating it.
_STOCK_PROFILE_TTL_SECONDS = 30 * 60
_stock_profile_cache: dict[str, tuple[float, StockProfileOut]] = {}
_stock_profile_cache_lock = threading.Lock()

# One lock per market so a slow stock scan doesn't hold up a quick forex/crypto one.
_triple_touch_scan_locks = {m: threading.Lock() for m in ("stocks", "forex", "crypto")}
_last_triple_touch_scan: dict[str, TripleTouchScanOut] = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"service": "Nasdaq Extreme Movers Bot API", "docs": "/docs"}


@app.get("/api/sectors")
def get_sectors():
    return VALID_SECTORS


@app.get("/api/status")
def get_status():
    return {
        "openai_configured": bool(settings.openai_api_key),
        "ai_enabled": license_module.is_ai_enabled(),
        "next_scans": next_run_times(),
        "scan_times": [settings.scan_time_1, settings.scan_time_2],
        "broken_scan_time": settings.broken_scan_time,
        "timezone": settings.scan_timezone,
    }


@app.get("/api/billing/status", response_model=BillingStatusOut)
def billing_status():
    return BillingStatusOut(
        plan=license_module.get_plan(),
        ai_enabled=license_module.is_ai_enabled(),
        paddle_client_token=settings.paddle_client_token,
        paddle_price_id=settings.paddle_price_id,
        paddle_environment=settings.paddle_environment,
    )


@app.post("/api/billing/dev-set-plan")
def dev_set_plan(plan: str = Query(..., pattern="^(free|pro)$"), token: str = Query(...)):
    # Local-only escape hatch to flip the plan without a real Paddle webhook.
    # Disabled unless PADDLE_DEV_OVERRIDE_TOKEN is set — never enable this in
    # a production deployment, since anyone with the token gets free AI access.
    if not settings.paddle_dev_override_token or token != settings.paddle_dev_override_token:
        raise HTTPException(status_code=403, detail="Dev plan override is not enabled.")
    lic = license_module.set_plan(plan)
    return {"plan": lic.plan}


@app.post("/api/paddle/webhook")
async def paddle_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("paddle-signature", "")
    if not paddle_module.verify_signature(raw_body, signature):
        raise HTTPException(status_code=401, detail="Invalid Paddle signature")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    paddle_module.handle_event(payload)
    return {"status": "ok"}


def _bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        return ""
    return authorization[7:].strip()


@app.post("/api/auth/google", response_model=AuthResultOut)
def auth_google(req: GoogleAuthIn):
    try:
        user, token = auth_module.login_with_google(req.credential)
    except auth_module.InvalidGoogleCredential as exc:
        raise HTTPException(status_code=401, detail=f"אימות Google נכשל: {exc}")
    return AuthResultOut(token=token, user=UserOut.model_validate(user))


@app.post("/api/auth/guest", response_model=AuthResultOut)
def auth_guest():
    user, token = auth_module.login_as_guest()
    return AuthResultOut(token=token, user=UserOut.model_validate(user))


@app.get("/api/auth/me", response_model=UserOut)
def auth_me(authorization: Optional[str] = Header(None)):
    user = auth_module.get_user_from_token(_bearer_token(authorization))
    if user is None:
        raise HTTPException(status_code=401, detail="לא מחובר.")
    return UserOut.model_validate(user)


@app.post("/api/auth/logout")
def auth_logout(authorization: Optional[str] = Header(None)):
    auth_module.logout(_bearer_token(authorization))
    return {"status": "ok"}


@app.get("/api/scans", response_model=list[ScanSummaryOut])
def list_scans(
    direction: str | None = None,
    category: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    q = db.query(Scan)
    if direction:
        q = q.filter(Scan.direction == direction)
    if category:
        q = q.filter(Scan.category == category)
    scans = q.order_by(desc(Scan.created_at)).limit(limit).all()
    return [
        ScanSummaryOut(
            id=s.id, direction=s.direction, category=s.category,
            created_at=s.created_at, result_count=len(s.results),
        )
        for s in scans
    ]


@app.get("/api/scans/latest", response_model=ScanOut)
def latest_scan(
    direction: str = Query(..., pattern="^(losers|gainers|broken)$"),
    category: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(Scan).filter(Scan.direction == direction)
    q = q.filter(Scan.category == category) if category else q.filter(Scan.category.is_(None))
    scan = q.order_by(desc(Scan.created_at)).first()
    if not scan:
        raise HTTPException(status_code=404, detail="עדיין אין סריקה שמורה לסינון הזה. הרץ סריקה ידנית כדי להתחיל.")
    return ScanOut.from_orm_full(scan)


@app.get("/api/scans/{scan_id}", response_model=ScanOut)
def get_scan(scan_id: int, db: Session = Depends(get_db)):
    scan = db.get(Scan, scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    return ScanOut.from_orm_full(scan)


@app.get("/api/technical-scan/latest", response_model=TechnicalScanOut)
def latest_technical_scan(timeframe: str = Query(DEFAULT_TIMEFRAME, pattern="^(5m|15m)$")):
    cached = _last_technical_scan.get(timeframe)
    if cached is None:
        raise HTTPException(status_code=404, detail="עדיין לא הרצת סריקה טכנית. לחץ על 'סרוק עכשיו' כדי להתחיל.")
    return cached


@app.post("/api/technical-scan/run", response_model=TechnicalScanOut)
def trigger_technical_scan(timeframe: str = Query(DEFAULT_TIMEFRAME, pattern="^(5m|15m)$")):
    # On-demand only, single-flight: a second click while a scan is already running
    # waits for it to finish and reuses its result rather than starting a duplicate.
    with _technical_scan_lock:
        result = scan_technical_signals(timeframe=timeframe)
        out = TechnicalScanOut(
            timeframe=result.timeframe,
            scanned_at=result.scanned_at,
            universe_stock_count=result.universe_stock_count,
            universe_forex_count=result.universe_forex_count,
            stocks=result.stocks,
            forex=result.forex,
        )
        _last_technical_scan[timeframe] = out
        return out


@app.get("/api/ema-breakout-scan/latest", response_model=EmaBreakoutScanOut)
def latest_ema_breakout_scan():
    if _last_ema_breakout_scan is None:
        raise HTTPException(status_code=404, detail="עדיין לא הרצת סריקת פריצת EMA50. לחץ על 'סרוק עכשיו' כדי להתחיל.")
    return _last_ema_breakout_scan


@app.post("/api/ema-breakout-scan/run", response_model=EmaBreakoutScanOut)
def trigger_ema_breakout_scan():
    global _last_ema_breakout_scan
    # On-demand only, single-flight: a second click while a scan is already running
    # waits for it to finish and reuses its result rather than starting a duplicate.
    with _ema_breakout_scan_lock:
        result = scan_ema50_breakout()
        out = EmaBreakoutScanOut(
            scanned_at=result.scanned_at,
            universe_stock_count=result.universe_stock_count,
            stocks=result.stocks,
        )
        _last_ema_breakout_scan = out
        return out


@app.get("/api/ema-touch-4h-scan/latest", response_model=EmaTouch4hScanOut)
def latest_ema_touch_4h_scan():
    if _last_ema_touch_4h_scan is None:
        raise HTTPException(status_code=404, detail="עדיין לא הרצת סריקת מגע EMA50 (4 שעות). לחץ על 'סרוק עכשיו' כדי להתחיל.")
    return _last_ema_touch_4h_scan


@app.post("/api/ema-touch-4h-scan/run", response_model=EmaTouch4hScanOut)
def trigger_ema_touch_4h_scan():
    global _last_ema_touch_4h_scan
    # On-demand only, single-flight: a second click while a scan is already running
    # waits for it to finish and reuses its result rather than starting a duplicate.
    with _ema_touch_4h_scan_lock:
        result = scan_ema50_touch_4h()
        out = EmaTouch4hScanOut(
            scanned_at=result.scanned_at,
            universe_stock_count=result.universe_stock_count,
            stocks=result.stocks,
        )
        _last_ema_touch_4h_scan = out
        return out


@app.get("/api/ema-double-touch-forex-scan/latest", response_model=EmaDoubleTouchForexScanOut)
def latest_ema_double_touch_forex_scan():
    if _last_ema_double_touch_forex_scan is None:
        raise HTTPException(status_code=404, detail="עדיין לא הרצת סריקת מגע כפול ב-EMA50 (פורקס). לחץ על 'סרוק עכשיו' כדי להתחיל.")
    return _last_ema_double_touch_forex_scan


@app.post("/api/ema-double-touch-forex-scan/run", response_model=EmaDoubleTouchForexScanOut)
def trigger_ema_double_touch_forex_scan():
    global _last_ema_double_touch_forex_scan
    # On-demand only, single-flight: a second click while a scan is already running
    # waits for it to finish and reuses its result rather than starting a duplicate.
    with _ema_double_touch_forex_scan_lock:
        result = scan_ema50_double_touch_forex_1h()
        out = EmaDoubleTouchForexScanOut(
            scanned_at=result.scanned_at,
            universe_forex_count=result.universe_forex_count,
            forex=result.forex,
        )
        _last_ema_double_touch_forex_scan = out
        return out


@app.get("/api/pattern-strategy-scan/latest", response_model=PatternScanOut)
def latest_pattern_strategy_scan():
    if _last_pattern_strategy_scan is None:
        raise HTTPException(status_code=404, detail="עדיין לא הרצת סריקת האסטרטגיה שלך. לחץ על 'סרוק עכשיו' כדי להתחיל.")
    return _last_pattern_strategy_scan


@app.post("/api/pattern-strategy-scan/run", response_model=PatternScanOut)
def trigger_pattern_strategy_scan():
    global _last_pattern_strategy_scan
    # On-demand only, single-flight: a second click while a scan is already running
    # waits for it to finish and reuses its result rather than starting a duplicate.
    with _pattern_strategy_scan_lock:
        result = scan_pattern_strategy()
        out = PatternScanOut(
            scanned_at=result.scanned_at,
            universe_stock_count=result.universe_stock_count,
            stocks=result.stocks,
        )
        _last_pattern_strategy_scan = out
        return out


@app.get("/api/triple-touch-scan/latest", response_model=TripleTouchScanOut)
def latest_triple_touch_scan(market: str = Query("stocks", pattern="^(stocks|forex|crypto)$")):
    cached = _last_triple_touch_scan.get(market)
    if cached is None:
        raise HTTPException(status_code=404, detail="עדיין לא הרצת סריקת 3 נגיעות ברמה. לחץ על 'סרוק עכשיו' כדי להתחיל.")
    return cached


@app.post("/api/triple-touch-scan/run", response_model=TripleTouchScanOut)
def trigger_triple_touch_scan(market: str = Query("stocks", pattern="^(stocks|forex|crypto)$")):
    # On-demand only, serialized per market: a second click while a scan is already
    # running waits for it rather than hammering Yahoo with two at once.
    with _triple_touch_scan_locks[market]:
        result = scan_triple_touch_1h(market)
        out = TripleTouchScanOut(
            market=result.market,
            scanned_at=result.scanned_at,
            universe_count=result.universe_count,
            results=result.results,
        )
        _last_triple_touch_scan[market] = out
        return out


@app.get("/api/candles", response_model=CandlesOut)
def candles(
    symbol: str = Query(..., min_length=1),
    timeframe: str = Query(DEFAULT_CANDLE_TIMEFRAME, pattern="^(5m|15m|30m|1h|4h|1d|1w)$"),
):
    data = get_candles(symbol.upper(), timeframe)
    if not data:
        raise HTTPException(status_code=404, detail="לא נמצאו נתוני נרות עבור המניה הזו.")
    return CandlesOut(symbol=symbol.upper(), timeframe=timeframe, candles=data)


@app.post("/api/scans/run", response_model=ScanOut)
def trigger_scan(req: RunScanRequest, db: Session = Depends(get_db)):
    if req.direction not in ("losers", "gainers", "broken"):
        raise HTTPException(status_code=400, detail="direction must be 'losers', 'gainers', or 'broken'")
    scan = run_scan(
        db,
        direction=req.direction,
        sector=req.sector,
        max_price=req.max_price,
        min_abs_percent=req.min_abs_percent,
    )
    return ScanOut.from_orm_full(scan)


@app.get("/api/symbol-search", response_model=SymbolSearchOut)
def symbol_search(q: str = Query(..., min_length=1)):
    matches = search_symbols(q)
    return SymbolSearchOut(
        query=q,
        results=[
            SymbolMatchOut(symbol=m.symbol, name=m.name, exchange=m.exchange, type=m.type)
            for m in matches
        ],
    )


@app.get("/api/quote", response_model=QuoteOut)
def quote(symbol: str = Query(..., min_length=1)):
    result = get_quote(symbol.upper())
    if result is None:
        raise HTTPException(status_code=404, detail="לא נמצא ציטוט עבור הסמל הזה.")
    return QuoteOut(
        symbol=result.symbol, name=result.name, price=result.price,
        change_percent=result.change_percent, market_cap=result.market_cap,
    )


@app.get("/api/crypto-scan", response_model=CryptoScanOut)
def crypto_scan():
    result = scan_crypto()
    return CryptoScanOut(
        scanned_at=datetime.utcnow(),
        results=[
            MarketCandidateOut(
                symbol=c.symbol, name=c.name, price=c.price, change_percent=c.change_percent,
                volume=c.volume, market_cap=c.market_cap,
                fifty_two_week_high=c.fifty_two_week_high, fifty_two_week_low=c.fifty_two_week_low,
                news=[
                    NewsItemOut(title=n.title, publisher=n.publisher, link=n.link, published_at=n.published_at)
                    for n in c.news
                ],
            )
            for c in result.candidates
        ],
    )


@app.get("/api/cap-scan", response_model=CapScanOut)
def cap_scan(size: str = Query(..., pattern="^(large|small)$")):
    result = scan_by_cap(size)
    return CapScanOut(
        size=size,
        scanned_at=datetime.utcnow(),
        total_matches=result.total_matches,
        results=[
            MarketCandidateOut(
                symbol=c.symbol, name=c.name, price=c.price, change_percent=c.change_percent,
                volume=c.volume, market_cap=c.market_cap,
            )
            for c in result.candidates[:40]
        ],
    )


@app.post("/api/chart-analysis", response_model=ChartAnalysisOut)
async def chart_analysis(image: UploadFile = File(...)):
    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="לא התקבלה תמונה.")
    result = analyze_chart_image(image_bytes, content_type=image.content_type or "image/png")
    if result is None:
        raise HTTPException(status_code=503, detail="ניתוח הגרף נכשל — ייתכן שאין מפתח OpenAI מוגדר בשרת.")
    return ChartAnalysisOut(
        pattern=result.pattern, confidence=result.confidence,
        bias=result.bias, explanation=result.explanation,
    )


@app.post("/api/chat", response_model=ChatReplyOut)
async def chat(messages: str = Form(...), image: Optional[UploadFile] = File(None)):
    try:
        parsed = json.loads(messages)
        if not isinstance(parsed, list):
            raise ValueError("messages must be a JSON array")
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="messages must be a JSON array of {role, content}")

    image_bytes = await image.read() if image else None
    reply, suggestions = generate_chat_reply(
        parsed,
        image_bytes=image_bytes,
        image_content_type=(image.content_type if image else "image/png") or "image/png",
    )
    return ChatReplyOut(reply=reply, suggestions=suggestions)


@app.get("/api/stock-profile", response_model=StockProfileOut)
def stock_profile(symbol: str = Query(..., min_length=1)):
    symbol = symbol.upper()
    now = time.time()
    with _stock_profile_cache_lock:
        cached = _stock_profile_cache.get(symbol)
    if cached and now - cached[0] < _STOCK_PROFILE_TTL_SECONDS:
        return cached[1]

    q = get_quote(symbol)
    if q is None:
        raise HTTPException(status_code=404, detail="לא נמצא ציטוט עבור הסמל הזה.")
    enrichment = enrich_symbol(symbol)
    direction = "losers" if (q.change_percent or 0) < 0 else "gainers"

    # The two AI calls are independent round-trips, so run them side by side.
    with ThreadPoolExecutor(max_workers=2) as pool:
        outlook_future = pool.submit(
            generate_outlook,
            symbol=symbol, name=q.name, price=q.price, change_percent=q.change_percent,
            direction=direction, enrichment=enrichment,
        )
        blurb_future = pool.submit(
            generate_company_blurb,
            symbol=symbol, name=q.name, sector=enrichment.sector, industry=enrichment.industry,
            business_summary=enrichment.business_summary,
        )
        ai_summary = outlook_future.result()
        company_blurb = blurb_future.result()

    profile = StockProfileOut(
        symbol=symbol, name=q.name, sector=enrichment.sector, industry=enrichment.industry,
        price=q.price, change_percent=q.change_percent, market_cap=q.market_cap,
        avg_volume=enrichment.avg_volume,
        target_mean_price=enrichment.target_mean_price,
        target_high_price=enrichment.target_high_price,
        target_low_price=enrichment.target_low_price,
        recommendation_key=enrichment.recommendation_key,
        num_analyst_opinions=enrichment.num_analyst_opinions,
        ai_summary=ai_summary, company_blurb=company_blurb,
        news=[
            NewsItemOut(title=n.title, publisher=n.publisher, link=n.link, published_at=n.published_at)
            for n in enrichment.news
        ],
    )
    with _stock_profile_cache_lock:
        _stock_profile_cache[symbol] = (now, profile)
    return profile


@app.post("/api/news-digest", response_model=NewsDigestOut)
def news_digest(req: NewsDigestIn):
    summary = generate_news_digest(req.symbol, [n.model_dump() for n in req.news])
    return NewsDigestOut(summary=summary)


@app.get("/api/world-news", response_model=WorldNewsOut)
def world_news(limit: int = Query(5, ge=1, le=10)):
    items = get_world_news(limit=limit)
    translated = generate_world_news_hebrew([{"title": n.title, "publisher": n.publisher} for n in items])
    return WorldNewsOut(news=[
        NewsItemOut(
            title=tr.get("title") or n.title,
            publisher=n.publisher,
            link=n.link,
            published_at=n.published_at,
            impact=tr.get("impact") or None,
        )
        for n, tr in zip(items, translated)
    ])


@app.post("/api/daily-digest", response_model=DailyDigestOut)
def daily_digest(req: DailyDigestIn, db: Session = Depends(get_db)):
    # The demo portfolio lives only in the browser (usePortfolio.js), so it
    # arrives in the request body; scan highlights are gathered here from the
    # DB (scheduled scans) and the in-memory caches of the on-demand scans.
    def _top_movers(direction: str, limit: int = 3) -> list[dict]:
        scan = (
            db.query(Scan)
            .filter(Scan.direction == direction, Scan.category.is_(None))
            .order_by(desc(Scan.created_at))
            .first()
        )
        if not scan:
            return []
        return [
            {"symbol": r.symbol, "change_percent": r.change_percent}
            for r in scan.results[:limit]
            if r.change_percent is not None
        ]

    broken_scan = (
        db.query(Scan)
        .filter(Scan.direction == "broken", Scan.category.is_(None))
        .order_by(desc(Scan.created_at))
        .first()
    )

    scans_summary = {
        "gainers": _top_movers("gainers"),
        "losers": _top_movers("losers"),
        "broken_count": len(broken_scan.results) if broken_scan else 0,
        "ema_breakout_count": len(_last_ema_breakout_scan.stocks) if _last_ema_breakout_scan else 0,
        "ema_touch_4h_count": len(_last_ema_touch_4h_scan.stocks) if _last_ema_touch_4h_scan else 0,
        "pattern_strategy_count": len(_last_pattern_strategy_scan.stocks) if _last_pattern_strategy_scan else 0,
    }

    summary = generate_daily_digest(req.cash, [h.model_dump() for h in req.holdings], scans_summary)
    return DailyDigestOut(summary=summary, generated_at=datetime.utcnow())
