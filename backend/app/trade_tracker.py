"""Watches every alert's trade (trade_plan.py) until price reaches its target or its stop,
then the alert's own bot replies to the alert with the outcome — the user's ask
(28/10/26).

Checked every few minutes (scheduler.py) on Yahoo's 5-minute bars — 1-minute for the
Nasdaq index, whose trades are scalps — from the bar the alert went out in, and only
while the symbol's market is open. A bar that reached both the stop and the target
counts as the stop: which came first inside it isn't known.

The reply is a picture of the chart at the moment it happened (the user's ask, 29/10/26):
the candles up to the one that reached the level, the trade's box from its entry, the
level marked — on the smallest candles that fit the trade's whole run."""
import html
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence
from zoneinfo import ZoneInfo

import yfinance as yf
from sqlalchemy.orm import Session

from . import alert_chart, mt5_trader, telegram
from .candles import Candle, _to_candles, get_candles
from .db import SessionLocal
from .models import TrackedTrade
from .technical_scan import _stock_fetch_limiter
from .trade_plan import TradePlan, first_hit

logger = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
DAY = 86400
# A trade opened longer ago than this is checked on the 60 days of 5m bars Yahoo keeps
# (after the server was off a while) — otherwise on the last 5 days'.
LONG_AGE_SECONDS = 4.5 * DAY
SESSION = ((9, 30), (16, 10))  # New York, for stocks and the index — a few minutes past the close
SEND_INTERVAL_SECONDS = 1.1
# The outcome picture's candles: the smallest of these (1m only for the index) that fit the
# trade, from entry to the level, in PICTURE_BARS.
PICTURE_TIMEFRAMES = (("1m", 60), ("5m", 300), ("15m", 900), ("1h", 3600), ("4h", 4 * 3600), ("1d", DAY))
PICTURE_BARS = 60

_run_lock = threading.Lock()


def send_alert(
    png: Optional[bytes], text: str, bot: str, key: str, symbol: str, name: Optional[str], plan: Optional[TradePlan]
) -> bool:
    """An alert from the bot named (telegram.bot_chat): its chart picture with the text as
    the caption, or the text alone when there's no picture. With a plan, its trade is
    watched from now on — and opened on MetaTrader 5 (mt5_trader.py), for the bots set
    there. Whether it went out."""
    token, chat = telegram.bot_chat(bot)
    if png is None:
        message_id = telegram.post_message(text, chat_id=chat, bot_token=token)
    else:
        message_id = telegram.post_photo(png, text, chat_id=chat, bot_token=token)
    if message_id is None:
        return False
    if plan is not None:
        track(key, symbol, name, bot, chat, message_id, plan)
        mt5_trader.open_trade(bot, symbol, plan)
    return True


def track(
    key: str, symbol: str, name: Optional[str], bot: str, chat_id: str, message_id: int, plan: TradePlan,
    now: Optional[float] = None, db: Optional[Session] = None,
):
    """Starts watching an alert's trade, from the 5m bar it went out in."""
    now = now or time.time()
    own = db is None
    db = db or SessionLocal()
    try:
        if db.query(TrackedTrade).filter(TrackedTrade.key == key).first():
            return
        db.add(TrackedTrade(
            key=key, symbol=symbol, name=name, bot=bot, chat_id=chat_id, message_id=message_id,
            direction=plan.direction, entry=plan.entry, stop=plan.stop, target=plan.target,
            checked_from=int(now // 300 * 300), created_at=datetime.fromtimestamp(now, timezone.utc).replace(tzinfo=None),
        ))
        db.commit()
    except Exception:
        logger.exception("Trade tracker: couldn't start watching %s", key)
    finally:
        if own:
            db.close()


def in_session(symbol: str, now: float) -> bool:
    """Whether the symbol's market is open: a forex pair from Sunday 21:00 to Friday 22:00
    UTC, a stock or the index in the US session."""
    if symbol.endswith("=X"):
        utc = datetime.fromtimestamp(now, timezone.utc)
        return not (utc.weekday() == 5 or (utc.weekday() == 6 and utc.hour < 21) or (utc.weekday() == 4 and utc.hour >= 22))
    ny = datetime.fromtimestamp(now, NY)
    return ny.weekday() < 5 and SESSION[0] <= (ny.hour, ny.minute) <= SESSION[1]


def fetch_bars(symbol: str, age: float) -> list[Candle]:
    """The symbol's recent bars, covering the last age seconds: 1m for the index, 5m for
    the rest. Yahoo includes the bar still in progress — what it reached so far counts."""
    if symbol.startswith("^") and age < LONG_AGE_SECONDS:
        interval, period = "1m", "5d"
    else:
        interval, period = "5m", "5d" if age < LONG_AGE_SECONDS else "60d"
    if not symbol.endswith("=X"):
        _stock_fetch_limiter.wait()
    try:
        df = yf.Ticker(symbol).history(interval=interval, period=period)
    except Exception:
        logger.warning("Trade tracker: %s %s history fetch failed", symbol, interval)
        return []
    return _to_candles(df) if df is not None and not df.empty else []


def outcome(trade: TrackedTrade, bars: Sequence[Candle]) -> Optional[tuple[str, int]]:
    """("target" or "stop", the bar's time) — whichever the bars from trade.checked_from
    on reached first."""
    return first_hit(trade, [b for b in bars if b.time >= trade.checked_from])


def picture_timeframe(symbol: str, opened: float, hit_time: int) -> str:
    sizes = PICTURE_TIMEFRAMES if symbol.startswith("^") else PICTURE_TIMEFRAMES[1:]
    return next((tf for tf, seconds in sizes if (hit_time - opened) / seconds <= PICTURE_BARS), sizes[-1][0])


def _picture_candles(symbol: str, timeframe: str) -> list[Candle]:
    if not symbol.endswith("=X"):
        _stock_fetch_limiter.wait()
    if timeframe != "1m":
        return get_candles(symbol, timeframe)
    try:
        df = yf.Ticker(symbol).history(interval="1m", period="5d")
    except Exception:
        logger.warning("Trade tracker: %s 1m history fetch failed", symbol)
        return []
    return _to_candles(df) if df is not None and not df.empty else []


def outcome_picture(trade: TrackedTrade, hit_time: int) -> Optional[bytes]:
    """PNG of the chart when the trade reached trade.outcome's level, in the bar starting
    hit_time; None if it can't be drawn — the reply then goes as text."""
    opened = trade.created_at.replace(tzinfo=timezone.utc).timestamp()
    timeframe = picture_timeframe(trade.symbol, opened, hit_time)
    try:
        candles = [c for c in _picture_candles(trade.symbol, timeframe) if c.time <= hit_time]
        entry = next((c for c in reversed(candles) if c.time <= opened), None)
        if entry is None:
            return None
        level = "Target" if trade.outcome == "target" else "Stop"
        return alert_chart.render(
            candles, f"{trade.name or trade.symbol} · {timeframe} · {level} hit", None, None, None,
            direction=trade.direction, closed=trade.outcome,
            plan=TradePlan(trade.direction, trade.entry, trade.stop, trade.target),
            plan_from=datetime.fromtimestamp(entry.time, timezone.utc).isoformat(),
            time_format="%d/%m" if timeframe == "1d" else "%d/%m %H:%M",
        )
    except Exception:
        logger.exception("Trade tracker: outcome picture failed for %s, replying with text only", trade.key)
        return None


def format_update(trade: TrackedTrade) -> str:
    name = html.escape(trade.name or trade.symbol)
    if trade.outcome == "target":
        return f"✅ טייק פרופיט · <b>{name}</b> · {telegram.price(round(trade.target, 5))}"
    return f"❌ סטופ לוס · <b>{name}</b> · {telegram.price(round(trade.stop, 5))}"


def check_trades(db: Session, now: Optional[float] = None, fetch: Callable[[str, float], list[Candle]] = fetch_bars) -> int:
    """Checks the open trades whose market is open, one fetch per symbol, and replies to
    the alert of each that reached its target or stop, with the chart's picture
    (outcome_picture); returns how many replies went out. One whose reply fails stays
    open, so the next run tries it again."""
    now = now or time.time()
    if not _run_lock.acquire(blocking=False):
        return 0
    try:
        by_symbol: dict[str, list[TrackedTrade]] = defaultdict(list)
        for t in db.query(TrackedTrade).filter(TrackedTrade.outcome.is_(None)):
            if in_session(t.symbol, now):
                by_symbol[t.symbol].append(t)
        sent = 0
        for symbol, trades in by_symbol.items():
            bars = fetch(symbol, now - min(t.checked_from for t in trades))
            if not bars:
                continue
            for t in trades:
                hit = outcome(t, bars)
                if hit is None:
                    # The last bar may still be in progress, so it's checked again next time.
                    t.checked_from = max(t.checked_from, bars[-1].time)
                    continue
                t.outcome, hit_time = hit
                if sent:
                    time.sleep(SEND_INTERVAL_SECONDS)
                token, _ = telegram.bot_chat(t.bot)
                png = outcome_picture(t, hit_time)
                if png is None:
                    message_id = telegram.post_message(format_update(t), chat_id=t.chat_id, bot_token=token, reply_to=t.message_id)
                else:
                    message_id = telegram.post_photo(png, format_update(t), chat_id=t.chat_id, bot_token=token, reply_to=t.message_id)
                if message_id is None:
                    t.outcome = None
                    continue
                t.closed_at = datetime.utcnow()
                sent += 1
            db.commit()
        if sent:
            logger.info("Trade tracker: %d trades closed", sent)
        return sent
    finally:
        _run_lock.release()
