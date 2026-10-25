"""Telegram alerts for forex pairs that broke out of a zone and then came back to touch
it three times, each time holding — on the 1h chart, breakouts up and breakdowns alike.

Zones and breakouts are the chart's own (see zones.py). Runs every 4 hours, on the 4h
candle closes (scheduler.py), and on demand; every setup is sent once, remembered in
the sent_alerts table."""
import html
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

import yfinance as yf
from sqlalchemy.orm import Session

from . import telegram
from .candles import _FETCH_PARAMS, MAX_CANDLES, Candle, _to_candles
from .config import settings
from .models import SentAlert
from .technical_scan import FOREX_PAIRS, TRIPLE_TOUCH_EXTRA_FOREX, _round_sig
from .zones import BREAK_BUFFER, LEAVE, RETEST_REACH, Breakout, Zone, detect_zones, find_breakouts

logger = logging.getLogger(__name__)

TIMEFRAME = "1h"
UNIVERSE = FOREX_PAIRS + TRIPLE_TOUCH_EXTRA_FOREX
# A price touched twice and turned from is a zone — what the chart window (trainer.html) draws.
ZONE_MIN_TOUCHES = 2
MIN_TOUCHES = 3
# A touch is a swing toward the zone: its high (low, after a breakout up) beats this
# many candles on each side. Price often hangs right under a broken zone and pokes it
# again and again (the user's GBP/CHF example, 21-22/09/26) — each poke is one touch.
TOUCH_BARS = 2
# Each touch has to come within this many candles of the one before it (the first, of
# the breakout). Price wandering back days later is just revisiting an old level, not
# retesting a breakout.
MAX_GAP_BARS = 30
# The alert fires on the MIN_TOUCHES-th touch, which must be at most this many candles
# old. The scan runs every 4 hours, so this covers one missed run.
MAX_AGE_BARS = 8
# Telegram allows about one message a second into the same chat.
SEND_INTERVAL_SECONDS = 1.1


@dataclass
class RetestSetup:
    symbol: str
    name: Optional[str]
    direction: str  # "up" = broke above the zone, touches it from above | "down" = broke below it
    zone_bottom: float
    zone_top: float
    zone_start_time: str  # the zone's first touch
    price: float
    breakout_time: str
    retest_times: list[str]  # oldest first; the MIN_TOUCHES-th is the one that triggered
    key: str  # one alert per setup: symbol + direction + breakout candle


@dataclass
class RetestScanResult:
    scanned_at: str
    universe_count: int
    results: list[RetestSetup]
    sent_count: int


_run_lock = threading.Lock()
_last_result: Optional[RetestScanResult] = None


def get_last_result() -> Optional[RetestScanResult]:
    return _last_result


def find_retests(candles: Sequence[Candle], zone: Zone, breakout: Breakout) -> list[int]:
    """Indices of the touches that followed a breakout, oldest first; empty if price has
    since closed back through the zone (the breakout failed).

    A touch is a candle that reaches back to the zone (within RETEST_REACH), closes on
    the breakout side of its middle, and is the swing extreme toward it: beyond the
    TOUCH_BARS candles before it (only those after the breakout — the breakout candle
    itself came from the other side) and not beaten by the TOUCH_BARS after it. The
    last candle or two can't have all their later candles yet, so a touch there counts
    on the ones there are — the alert should come while price is still at the zone."""
    up = breakout.dir == "up"
    buffer = BREAK_BUFFER * zone.unit
    reach = RETEST_REACH * zone.unit
    touches: list[int] = []
    last = breakout.idx
    for j in range(breakout.idx + 1, len(candles)):
        c = candles[j]
        if (c.close < zone.bottom - buffer) if up else (c.close > zone.top + buffer):
            return []
        if j - last > MAX_GAP_BARS:
            break
        before = candles[max(breakout.idx + 1, j - TOUCH_BARS) : j]
        after = candles[j + 1 : j + 1 + TOUCH_BARS]
        if up:
            swing = all(k.low > c.low for k in before) and all(k.low >= c.low for k in after)
            touched = c.low <= zone.top + reach and c.close > zone.mid
        else:
            swing = all(k.high < c.high for k in before) and all(k.high <= c.high for k in after)
            touched = c.high >= zone.bottom - reach and c.close < zone.mid
        if swing and touched:
            touches.append(j)
            last = j
    return touches


def find_setup(candles: Sequence[Candle]) -> Optional[tuple[Zone, Breakout, list[int]]]:
    """The setup that just completed on these candles: a breakout whose MIN_TOUCHES-th
    touch is within MAX_AGE_BARS of the last candle. When several qualify (e.g. one
    candle broke through stacked zones), the most recent one wins, then the stronger zone."""
    nth = MIN_TOUCHES - 1
    best = None
    for zone in detect_zones(candles, min_touches=ZONE_MIN_TOUCHES):
        for breakout in find_breakouts(candles, zone):
            retests = find_retests(candles, zone, breakout)
            if len(retests) < MIN_TOUCHES or len(candles) - 1 - retests[nth] > MAX_AGE_BARS:
                continue
            if best is None or retests[nth] > best[2][nth]:
                best = (zone, breakout, retests)
    return best


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _evaluate(symbol: str, name: Optional[str]) -> Optional[RetestSetup]:
    interval, period = _FETCH_PARAMS[TIMEFRAME]  # the chart's 1h view, so the zones match what it draws
    try:
        df = yf.Ticker(symbol).history(interval=interval, period=period)
    except Exception:
        logger.warning("Retest alert scan: 1h history fetch failed for %s", symbol)
        return None
    if df is None or df.empty:
        return None
    candles = _to_candles(df)
    # Yahoo includes the hour still in progress; a touch only counts on a closed candle.
    if candles and candles[-1].time + 3600 > time.time():
        candles.pop()
    candles = candles[-MAX_CANDLES:]

    found = find_setup(candles)
    if found is None:
        return None
    zone, breakout, retests = found
    up = breakout.dir == "up"
    leave = LEAVE * zone.unit
    after = candles[retests[-1] + 1:]
    # Only pairs still sitting on the zone — once price has pulled clear of it again
    # after the last touch, the entry is gone (the user asked for these alone).
    if any(c.close > zone.top + leave if up else c.close < zone.bottom - leave for c in after):
        return None
    breakout_time = _iso(candles[breakout.idx].time)
    return RetestSetup(
        symbol=symbol,
        name=name,
        direction=breakout.dir,
        zone_bottom=_round_sig(zone.bottom),
        zone_top=_round_sig(zone.top),
        zone_start_time=_iso(candles[zone.start_idx].time),
        price=_round_sig(candles[-1].close),
        breakout_time=breakout_time,
        retest_times=[_iso(candles[i].time) for i in retests],
        key=f"retest3:{TIMEFRAME}:{symbol}:{breakout.dir}:{breakout_time}",
    )


def scan_retest_setups() -> tuple[int, list[RetestSetup]]:
    results: list[RetestSetup] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate, sym, name) for sym, name in UNIVERSE]
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                results.append(result)
    results.sort(key=lambda s: s.retest_times[-1], reverse=True)
    return len(UNIVERSE), results


def _local_time(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(settings.alert_timezone)).strftime("%d/%m %H:%M")


def chart_url(s: RetestSetup) -> str:
    return telegram.chart_window_url(
        s.symbol, TIMEFRAME, s.zone_bottom, s.zone_top, s.zone_start_time,
        direction=s.direction, breakout=s.breakout_time, retests=s.retest_times,
    )


def format_message(s: RetestSetup) -> str:
    head = "🟢 פריצה למעלה" if s.direction == "up" else "🔴 שבירה למטה"
    lines = [
        f"{head} + {len(s.retest_times)} נגיעות · <b>{html.escape(s.name or s.symbol)}</b> (שעה)",
        f"אזור: {telegram.price(s.zone_bottom)}–{telegram.price(s.zone_top)}",
        f"פריצה: {_local_time(s.breakout_time)}",
        f"נגיעות: {', '.join(_local_time(t) for t in s.retest_times)}",
        f"מחיר: {telegram.price(s.price)} · עדיין על האזור",
        f"{telegram.link(telegram.tradingview_url(s.symbol), 'TradingView')}"
        f" · {telegram.link(chart_url(s), 'בסורק עם הסימון')}",
    ]
    return "\n".join(lines)


def run_retest_alerts(db: Session) -> RetestScanResult:
    """Scans, then sends every setup not alerted before. A setup whose send fails isn't
    recorded, so the next run tries it again."""
    global _last_result
    with _run_lock:
        universe_count, results = scan_retest_setups()
        sent = 0
        if telegram.is_configured():
            already = {k for (k,) in db.query(SentAlert.key).filter(SentAlert.key.in_([s.key for s in results]))}
            for s in results:
                if s.key in already:
                    continue
                if sent:
                    time.sleep(SEND_INTERVAL_SECONDS)
                if telegram.send_message(format_message(s)):
                    db.add(SentAlert(key=s.key, symbol=s.symbol))
                    db.commit()
                    sent += 1
        else:
            logger.info("Retest alert scan: Telegram not configured, %d setups found but not sent", len(results))
        logger.info("Retest alert scan: %d setups in %d pairs, %d alerts sent", len(results), universe_count, sent)
        _last_result = RetestScanResult(
            scanned_at=datetime.now(timezone.utc).isoformat(),
            universe_count=universe_count,
            results=results,
            sent_count=sent,
        )
        return _last_result
