"""Telegram alerts for stocks that broke out of a zone and then came back to retest it
three times, each time holding — on the daily chart, breakouts up and breakdowns alike.

Zones and breakouts are the chart's own (see zones.py). Runs once a day after the US
close (scheduler.py) and on demand; every setup is sent once, remembered in the
sent_alerts table."""
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
from .candles import MAX_CANDLES, Candle, _to_candles
from .config import settings
from .models import SentAlert
from .technical_scan import _get_stock_universe, _round_sig, _stock_fetch_limiter
from .zones import (
    BREAK_BUFFER,
    CONFIRM_BARS,
    FAST_RETEST_BARS,
    LEAVE,
    RETEST_REACH,
    Breakout,
    Zone,
    detect_zones,
    find_breakouts,
)

logger = logging.getLogger(__name__)

DAILY_PERIOD = "2y"  # the chart's 1d view, so the zones match what it draws
# A price touched twice and turned from is a zone — what the chart window (trainer.html) draws.
ZONE_MIN_TOUCHES = 2
MIN_RETESTS = 3
# Each retest has to come within this many candles of the one before it (the first, of
# the breakout) — zones.js's MAX_RETEST_BARS. Price wandering back months later is
# just revisiting an old level, not retesting a breakout.
MAX_GAP_BARS = 30
# The alert fires on the MIN_RETESTS-th retest, which must be at most this many candles
# old — a setup that got there weeks ago (and is on its 5th test by now) is old news. A
# missed day or two of scans still catches it.
MAX_AGE_BARS = 3
# Telegram allows about one message a second into the same chat.
SEND_INTERVAL_SECONDS = 1.1


@dataclass
class RetestSetup:
    symbol: str
    name: Optional[str]
    direction: str  # "up" = broke above the zone, retests from above | "down" = broke below it
    zone_bottom: float
    zone_top: float
    zone_start_time: str  # the zone's first touch
    price: float
    breakout_time: str
    retest_times: list[str]  # oldest first; the MIN_RETESTS-th is the one that triggered
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


def _is_quick_retest(candles: Sequence[Candle], zone: Zone, breakout: Breakout, j: int) -> bool:
    """zones.js's quick retest, before price ever got clear: the 2nd to
    FAST_RETEST_BARS-th candle after the breakout pulls back past the previous candle's
    extreme to the zone itself and closes on the breakout side of its middle, and within
    CONFIRM_BARS a close gets past its far end and LEAVE clear of the zone."""
    if not 2 <= j - breakout.idx <= FAST_RETEST_BARS:
        return False
    up = breakout.dir == "up"
    buffer = BREAK_BUFFER * zone.unit
    leave = LEAVE * zone.unit
    c, prev = candles[j], candles[j - 1]
    if up:
        pullback = c.low < prev.low and c.low <= zone.top and c.close > zone.mid
    else:
        pullback = c.high > prev.high and c.high >= zone.bottom and c.close < zone.mid
    if not pullback:
        return False
    for k in candles[j + 1 : j + 1 + CONFIRM_BARS]:
        if (k.close < zone.bottom - buffer) if up else (k.close > zone.top + buffer):
            return False
        if (k.close > max(c.high, zone.top + leave)) if up else (k.close < min(c.low, zone.bottom - leave)):
            return True
    return False


def find_retests(candles: Sequence[Candle], zone: Zone, breakout: Breakout) -> list[int]:
    """Indices of the retests that followed a breakout, oldest first; empty if price has
    since closed back through the zone (the breakout failed).

    Same retest rule as zones.js: once price has closed LEAVE typical candles clear of
    the zone, a retest is a candle that reaches back to it and closes on the breakout
    side of its middle; the first one can also be a quick retest (_is_quick_retest).
    The next retest only counts after price has pulled clear again, so a few candles
    sitting on the zone are one retest, not several."""
    up = breakout.dir == "up"
    buffer = BREAK_BUFFER * zone.unit
    reach = RETEST_REACH * zone.unit
    leave = LEAVE * zone.unit
    retests: list[int] = []
    left = False
    last = breakout.idx
    for j in range(breakout.idx + 1, len(candles)):
        c = candles[j]
        if (c.close < zone.bottom - buffer) if up else (c.close > zone.top + buffer):
            return []
        if j - last > MAX_GAP_BARS:
            break
        if not left:
            if not retests and _is_quick_retest(candles, zone, breakout, j):
                retests.append(j)
                last = j
                continue
            left = c.close > zone.top + leave if up else c.close < zone.bottom - leave
            continue
        reached = c.low <= zone.top + reach if up else c.high >= zone.bottom - reach
        held = c.close > zone.mid if up else c.close < zone.mid
        if reached and held:
            retests.append(j)
            last = j
            left = False
    return retests


def find_setup(candles: Sequence[Candle]) -> Optional[tuple[Zone, Breakout, list[int]]]:
    """The setup that just completed on these candles: a breakout whose MIN_RETESTS-th
    retest is within MAX_AGE_BARS of the last candle. When several qualify (e.g. one
    candle broke through stacked zones), the most recent one wins, then the stronger zone."""
    nth = MIN_RETESTS - 1
    best = None
    for zone in detect_zones(candles, min_touches=ZONE_MIN_TOUCHES):
        for breakout in find_breakouts(candles, zone):
            retests = find_retests(candles, zone, breakout)
            if len(retests) < MIN_RETESTS or len(candles) - 1 - retests[nth] > MAX_AGE_BARS:
                continue
            if best is None or retests[nth] > best[2][nth]:
                best = (zone, breakout, retests)
    return best


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _evaluate(symbol: str, name: Optional[str]) -> Optional[RetestSetup]:
    _stock_fetch_limiter.wait()
    try:
        df = yf.Ticker(symbol).history(interval="1d", period=DAILY_PERIOD)
    except Exception:
        logger.warning("Retest alert scan: daily history fetch failed for %s", symbol)
        return None
    if df is None or df.empty:
        return None
    candles = _to_candles(df)[-MAX_CANDLES:]

    found = find_setup(candles)
    if found is None:
        return None
    zone, breakout, retests = found
    up = breakout.dir == "up"
    leave = LEAVE * zone.unit
    after = candles[retests[-1] + 1:]
    # Only stocks still sitting on the zone — once price has pulled clear of it again
    # after the last retest, the entry is gone (the user asked for these alone).
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
        key=f"retest3:1d:{symbol}:{breakout.dir}:{breakout_time[:10]}",
    )


def scan_retest_setups() -> tuple[int, list[RetestSetup]]:
    universe = _get_stock_universe(
        settings.retest_alert_stock_universe_size, min_volume=settings.retest_alert_min_stock_volume
    )
    if not universe:
        logger.warning("Retest alert scan: empty stock universe (screener unavailable?)")
    results: list[RetestSetup] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate, sym, name) for sym, name in universe]
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                results.append(result)
    results.sort(key=lambda s: s.retest_times[-1], reverse=True)
    return len(universe), results


def _local_date(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(settings.scan_timezone)).strftime("%d/%m/%y")


def _price(v: float) -> str:
    return f"{v:,.2f}" if v >= 1 else f"{v:.4f}"


def chart_url(s: RetestSetup) -> str:
    return telegram.chart_window_url(
        s.symbol, "1d", s.zone_bottom, s.zone_top, s.zone_start_time,
        direction=s.direction, breakout=s.breakout_time, retests=s.retest_times,
    )


def format_message(s: RetestSetup) -> str:
    head = "🟢 פריצה למעלה" if s.direction == "up" else "🔴 שבירה למטה"
    lines = [f"{head} + {len(s.retest_times)} ריטסטים · <b>{html.escape(s.symbol)}</b> (יומי)"]
    if s.name:
        lines.append(html.escape(s.name))
    lines += [
        f"אזור: {_price(s.zone_bottom)}–{_price(s.zone_top)}",
        f"פריצה: {_local_date(s.breakout_time)}",
        f"ריטסטים: {', '.join(_local_date(t) for t in s.retest_times)}",
        f"מחיר: {_price(s.price)} · עדיין על האזור",
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
        logger.info("Retest alert scan: %d setups in %d stocks, %d alerts sent", len(results), universe_count, sent)
        _last_result = RetestScanResult(
            scanned_at=datetime.now(timezone.utc).isoformat(),
            universe_count=universe_count,
            results=results,
            sent_count=sent,
        )
        return _last_result
