"""Penny stocks that exploded today: under $5, closed up 20% or more on at least three
times their usual volume. Of the two ways offered, the user picked (28/10/26) a list once
a day after the US close, as prep for the next session — not live alerts during it.
Every stock goes to its own Telegram bot as its own alert: the daily chart with the day's
candle marked, and a short caption.

One Yahoo screener call finds them all (its quotes carry the 3-month average volume
too), so the scan takes seconds; only the alerts' charts cost a request each.

The daily list isn't scheduled since 29/10/26. Since 2/11/26 the same scan runs live
instead (the user's pick): every 5 minutes of the session, a stock goes to the "שונות" bot
the moment it first passes, once a day (run_penny_live). By then it has to have traded
only its usual full day's volume (LIVE_MIN_VOLUME_RATIO) — ×3 is rarely reached before
the afternoon."""
import html
import logging
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from yfinance import EquityQuery

from . import alert_chart, telegram
from .candles import get_candles
from .config import settings
from .models import SentAlert
from .scanner import _DERIVATIVE_NAME_RE, _fetch_all_quotes
from .zone_touch_scan import STOCK_EXCHANGES

logger = logging.getLogger(__name__)

MAX_PRICE = 5.0
# Under a dime it's sub-penny OTC junk, where a single trade moves the price 50%.
MIN_PRICE = 0.10
MIN_CHANGE_PERCENT = 20.0
MIN_VOLUME_RATIO = 3.0  # the day's volume over its 3-month daily average
LIVE_MIN_VOLUME_RATIO = 1.0  # during the session: the volume so far
# Dollars traded on the day — with less, the stock can't really be bought or sold.
MIN_DOLLAR_VOLUME = 500_000
# The screener keeps listing quotes that haven't traded for days; only those from the
# latest session (this close to the newest quote) count.
SESSION_HOURS = 12
FETCH_CAP = 500
MAX_ALERTS = 10
SEND_INTERVAL_SECONDS = 1.1
SESSION_OPEN = (9, 30)  # New York
SESSION_CLOSE = (16, 0)


@dataclass
class PennySpike:
    symbol: str
    name: Optional[str]
    price: float
    change_percent: float
    volume: int
    volume_ratio: float


@dataclass
class PennyScanResult:
    scanned_at: str
    session: Optional[date]  # the trading day the list is from (US/Eastern)
    results: list[PennySpike]
    sent_count: int = 0


_run_lock = threading.Lock()


def is_derivative(symbol: str, name: Optional[str]) -> bool:
    """A warrant, right or unit: a fifth letter W/R/U/Z on a Nasdaq symbol, a suffix after
    a dash (NYSE's "-WT", "-U"), a name that says so, or no name at all (OPENZ). Narrower
    than scanner._is_derivative, which also drops real four-letter tickers like NEXR."""
    return (
        (len(symbol) == 5 and symbol[-1] in "WRUZ")
        or "-" in symbol
        or not name
        or bool(_DERIVATIVE_NAME_RE.search(name))
    )


def spikes_from_quotes(
    quotes: Sequence[dict], min_volume_ratio: float = MIN_VOLUME_RATIO
) -> tuple[list[PennySpike], Optional[date]]:
    """The screener's quotes that pass every rule, biggest gain first, and the session
    they're from."""
    times = [q["regularMarketTime"] for q in quotes if q.get("regularMarketTime")]
    if not times:
        return [], None
    newest = max(times)
    out: list[PennySpike] = []
    for q in quotes:
        symbol = q.get("symbol")
        name = q.get("shortName") or q.get("longName")
        price = q.get("regularMarketPrice") or 0
        change = q.get("regularMarketChangePercent") or 0
        volume = q.get("regularMarketVolume") or 0
        average = q.get("averageDailyVolume3Month") or 0
        if not symbol or is_derivative(symbol, name):
            continue
        if newest - (q.get("regularMarketTime") or 0) > SESSION_HOURS * 3600:
            continue
        if not (MIN_PRICE <= price < MAX_PRICE) or change < MIN_CHANGE_PERCENT:
            continue
        if not average or volume < min_volume_ratio * average or price * volume < MIN_DOLLAR_VOLUME:
            continue
        out.append(PennySpike(symbol, name, price, round(change, 1), int(volume), round(volume / average, 1)))
    out.sort(key=lambda s: -s.change_percent)
    session = datetime.fromtimestamp(newest, ZoneInfo(settings.scan_timezone)).date()
    return out, session


def scan_penny_spikes(min_volume_ratio: float = MIN_VOLUME_RATIO) -> PennyScanResult:
    query = EquityQuery("and", [
        EquityQuery("eq", ["region", "us"]),
        EquityQuery("is-in", ["exchange", *STOCK_EXCHANGES]),
        EquityQuery("lt", ["intradayprice", MAX_PRICE]),
        EquityQuery("gt", ["percentchange", MIN_CHANGE_PERCENT]),
        EquityQuery("gt", ["dayvolume", MIN_DOLLAR_VOLUME / MAX_PRICE]),
    ])
    try:
        quotes = _fetch_all_quotes(query, sort_field="percentchange", sort_asc=False, fetch_cap=FETCH_CAP)
    except Exception:
        logger.exception("Penny scan: screener unavailable")
        quotes = []
    results, session = spikes_from_quotes(quotes, min_volume_ratio)
    return PennyScanResult(scanned_at=datetime.now(timezone.utc).isoformat(), session=session, results=results)


def format_message(s: PennySpike, live: bool = False) -> str:
    move = (
        f"עולה עכשיו {s.change_percent:.0f}% · מחזור עד כה פי {s.volume_ratio:.1f} מיום רגיל" if live
        else f"עלתה {s.change_percent:.0f}% · מחזור פי {s.volume_ratio:.0f} מהרגיל"
    )
    return (
        f"🚀 <b>{html.escape(s.symbol)}</b> · ${telegram.price(s.price)}\n"
        f"{move}\n"
        f"{telegram.link(telegram.tradingview_url(s.symbol), 'TradingView')}"
    )


def send_alert(s: PennySpike, session: Optional[date], live: bool = False) -> bool:
    """The stock's daily chart with the session's candle marked, the message as its
    caption — or the message alone if the picture can't be drawn."""
    bot, chat = settings.telegram_penny_bot_token, settings.telegram_penny_chat_id or None
    text = format_message(s, live)
    try:
        candles = get_candles(s.symbol, "1d")
        tz = ZoneInfo(settings.scan_timezone)
        last = candles[-1]
        spike = (
            datetime.fromtimestamp(last.time, timezone.utc).isoformat()
            if datetime.fromtimestamp(last.time, tz).date() == session else None
        )
        png = alert_chart.render(
            candles, f"{s.symbol} · 1d", None, None, None, direction="up", breakout=spike, time_format="%d/%m",
        )
    except Exception:
        logger.exception("Penny scan: chart picture failed for %s, sending text only", s.symbol)
        return telegram.send_message(text, chat_id=chat, bot_token=bot)
    return telegram.send_photo(png, text, chat_id=chat, bot_token=bot)


def run_penny_scan(send: bool = False, today_only: bool = False) -> PennyScanResult:
    """Scans; with send, also sends each of the top MAX_ALERTS to the penny bot as its own
    alert. today_only (the daily run) sends nothing when the latest session isn't today's:
    on a market holiday the screener still holds the day before's list, already sent."""
    with _run_lock:
        result = scan_penny_spikes()
        today = datetime.now(ZoneInfo(settings.scan_timezone)).date()
        if send and today_only and result.session != today:
            logger.info("Penny scan: no session today (latest %s), alerts not sent", result.session)
        elif send:
            if telegram.is_configured() and settings.telegram_penny_bot_token:
                for i, s in enumerate(result.results[:MAX_ALERTS]):
                    if i:
                        time.sleep(SEND_INTERVAL_SECONDS)
                    result.sent_count += send_alert(s, result.session)
            else:
                logger.info("Penny scan: no TELEGRAM_PENNY_BOT_TOKEN, alerts not sent")
        logger.info("Penny scan: %d penny stocks exploded in the %s session", len(result.results), result.session)
        return result


def run_penny_live(db: Session) -> int:
    """During the session: sends each stock that passes the live rules, once a day, up to
    MAX_ALERTS a run; returns how many went out. One whose send fails isn't recorded, so
    the next run tries it again."""
    now = datetime.now(ZoneInfo(settings.scan_timezone))
    if now.weekday() >= 5 or not SESSION_OPEN <= (now.hour, now.minute) < SESSION_CLOSE:
        return 0
    if not (telegram.is_configured() and settings.telegram_penny_bot_token):
        return 0
    with _run_lock:
        result = scan_penny_spikes(LIVE_MIN_VOLUME_RATIO)
        if result.session != now.date():  # a market holiday: the list is an old session's
            return 0
        keys = {s.symbol: f"penny-live:{s.symbol}:{result.session}" for s in result.results}
        already = {k for (k,) in db.query(SentAlert.key).filter(SentAlert.key.in_(list(keys.values())))}
        sent = 0
        for s in result.results:
            if keys[s.symbol] in already or sent >= MAX_ALERTS:
                continue
            if sent:
                time.sleep(SEND_INTERVAL_SECONDS)
            if send_alert(s, result.session, live=True):
                db.add(SentAlert(key=keys[s.symbol], symbol=s.symbol))
                db.commit()
                sent += 1
        if sent:
            logger.info("Penny live: %d alerts sent", sent)
        return sent


if __name__ == "__main__":
    # `python -m app.penny_scan` lists the latest session's spikes; add "send" to send them.
    logging.basicConfig(level=logging.INFO)
    r = run_penny_scan(send="send" in sys.argv[1:])
    for s in r.results:
        print(f"{s.symbol:8} ${s.price:<8g} +{s.change_percent:<6} x{s.volume_ratio:<6} {s.name}")
    print(f"{len(r.results)} in the {r.session} session, {r.sent_count} sent")
