"""Forex pairs whose 4h chart has a zone price turned at three or more times, with the
last candle closing inside that zone — price is still sitting on it, not already gone.

Zones are the chart's own (zones.py, what the trainer's automatic marking draws), so a
pair in the list shows the same zone when opened on the 4h chart. Listed in the
trainer's scanner ("3 נגיעות") on demand, and sent to Telegram as one list once a day
after the US close (scheduler.py). Stocks were scanned too until the user narrowed the
alerts to forex (October 2026)."""
import html
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from . import telegram
from .candles import Candle, _to_candles, get_4h_dataframe
from .config import settings
from .technical_scan import FOREX_PAIRS, TRIPLE_TOUCH_EXTRA_FOREX, _round_sig
from .zones import RETEST_REACH, Zone, detect_zones

logger = logging.getLogger(__name__)

UNIVERSE = FOREX_PAIRS + TRIPLE_TOUCH_EXTRA_FOREX
HISTORY_PERIOD = "2mo"  # 1h bars → ~250 four-hour candles of a pair (6 a day)
# Zones come from the last this-many 4h candles (~3 weeks of a pair), so every touch is
# recent — not a level price last turned months ago.
WINDOW_BARS = 100
MIN_TOUCHES = 3
# The last close may be this many typical candles past the zone's edge and still count
# as "in" it — zones are thin, and a close a hair outside is still sitting on the level.
IN_ZONE_REACH = RETEST_REACH
# Telegram gets only the top of the list (most touches first) — the user asked for 10
# while judging whether the scan is any good; the full ~130 stocks a day was too much.
MAX_ALERT_PAIRS = 10
# Telegram caps a message at 4096 characters; the list is split below that.
MAX_MESSAGE_CHARS = 3800
SEND_INTERVAL_SECONDS = 1.1


@dataclass
class ZoneTouchSetup:
    symbol: str
    name: Optional[str]
    price: float
    zone_bottom: float
    zone_top: float
    touch_count: int
    touch_times: list[str]  # the swing candle of each touch, oldest first
    distance_pct: float  # last close's distance from the zone's middle, % of price


@dataclass
class ZoneTouchScanResult:
    scanned_at: str
    universe_count: int
    results: list[ZoneTouchSetup]
    sent: bool = False


_run_lock = threading.Lock()
_last_result: Optional[ZoneTouchScanResult] = None


def get_last_result() -> Optional[ZoneTouchScanResult]:
    return _last_result


def find_zone(candles: Sequence[Candle]) -> Optional[Zone]:
    """The strongest zone with MIN_TOUCHES+ touches that the last close is inside."""
    last = candles[-1].close
    for zone in detect_zones(candles, min_touches=MIN_TOUCHES):  # strongest first
        reach = IN_ZONE_REACH * zone.unit
        if zone.bottom - reach <= last <= zone.top + reach:
            return zone
    return None


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _evaluate(symbol: str, name: Optional[str]) -> Optional[ZoneTouchSetup]:
    df = get_4h_dataframe(symbol, period=HISTORY_PERIOD)
    if df is None or df.empty:
        return None
    candles = _to_candles(df)[-WINDOW_BARS:]
    if len(candles) < WINDOW_BARS // 2:
        return None

    zone = find_zone(candles)
    if zone is None:
        return None
    last = candles[-1].close
    return ZoneTouchSetup(
        symbol=symbol,
        name=name,
        price=_round_sig(last),
        zone_bottom=_round_sig(zone.bottom),
        zone_top=_round_sig(zone.top),
        touch_count=len(zone.touch_idx),
        touch_times=[_iso(candles[i].time) for i in zone.touch_idx],
        distance_pct=round(abs(last - zone.mid) / last * 100, 2),
    )


def scan_zone_touches() -> ZoneTouchScanResult:
    results: list[ZoneTouchSetup] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate, sym, name) for sym, name in UNIVERSE]
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                results.append(result)
    results.sort(key=lambda s: (-s.touch_count, s.distance_pct))
    return ZoneTouchScanResult(
        scanned_at=datetime.now(timezone.utc).isoformat(),
        universe_count=len(UNIVERSE),
        results=results,
    )


def format_messages(result: ZoneTouchScanResult) -> list[str]:
    day = datetime.fromisoformat(result.scanned_at).astimezone(ZoneInfo(settings.alert_timezone)).strftime("%d/%m/%y")
    head = f"📍 <b>3+ נגיעות באזור · 4 שעות</b> · {day}"
    if not result.results:
        return [f"{head}\nאין היום צמדים שהמחיר שלהם עדיין בתוך אזור כזה."]
    shown = result.results[:MAX_ALERT_PAIRS]
    count = f"{len(result.results)} צמדים — המחיר עדיין בתוך האזור"
    if len(shown) < len(result.results):
        count += f". {len(shown)} עם הכי הרבה נגיעות"
    lines = [f"{head}\n{count}:"]
    for s in shown:
        chart = telegram.chart_window_url(
            s.symbol, "4h", s.zone_bottom, s.zone_top, s.touch_times[0], touches=s.touch_times
        )
        lines.append(
            f"<b>{html.escape(s.name or s.symbol)}</b> {telegram.price(s.price)}"
            f" · אזור {telegram.price(s.zone_bottom)}–{telegram.price(s.zone_top)} · ×{s.touch_count}\n"
            f"{telegram.link(telegram.tradingview_url(s.symbol), 'TradingView')}"
            f" · {telegram.link(chart, 'בסורק עם הסימון')}"
        )
    messages, current = [], ""
    for line in lines:
        if current and len(current) + 1 + len(line) > MAX_MESSAGE_CHARS:
            messages.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    messages.append(current)
    return messages


def run_zone_touch_scan(send: bool = False) -> ZoneTouchScanResult:
    """Scans and caches the result for the trainer; with send, also posts the list to
    Telegram (the daily run)."""
    global _last_result
    with _run_lock:
        result = scan_zone_touches()
        if send:
            if telegram.is_configured():
                ok = True
                for i, text in enumerate(format_messages(result)):
                    if i:
                        time.sleep(SEND_INTERVAL_SECONDS)
                    ok = telegram.send_message(text) and ok
                result.sent = ok
            else:
                logger.info("Zone touch scan: Telegram not configured, list not sent")
        logger.info("Zone touch scan: %d pairs in a 3-touch 4h zone out of %d", len(result.results), result.universe_count)
        _last_result = result
        return result
