"""Telegram alerts for forex pairs that broke out of a zone and then came back to touch
it three times, each time holding — on the 1h chart, breakouts up and breakdowns alike.
The user calls them "אסטרטגיה 1" (the 4h zone list is "אסטרטגיה 4").

Zones and breakouts are the chart's own (see zones.py), the touches retests.py's. Sent
on the entry candle after the third touch, with its trade (trade_plan.py: stop behind
the zone, 1:3) — only when the daily candle agrees with the trade's direction
(daily_candle.py), and not while price is ranging between this zone and an as strong
one close by (is_ranging). Runs every hour, on the 1h candle closes (scheduler.py), and
on demand; every setup is sent once, remembered in the sent_alerts table."""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

import yfinance as yf
from sqlalchemy.orm import Session

from . import alert_chart, telegram, trade_tracker
from .candles import _FETCH_PARAMS, MAX_CANDLES, Candle, _to_candles
from .config import settings
from .daily_candle import confirms, daily_candles
from .models import SentAlert
from .retests import ZONE_MIN_TOUCHES, find_retests
from .technical_scan import ALERT_FOREX, _round_sig
from .trade_plan import TradePlan, is_entry_candle, zone_plan
from .zone_touch_scan import is_ranging
from .zones import LEAVE, Breakout, Zone, detect_zones, find_breakouts

logger = logging.getLogger(__name__)

TIMEFRAME = "1h"
UNIVERSE = ALERT_FOREX  # Telegram only — the watchlist pairs
MIN_TOUCHES = 3
# The alert fires on the entry candle (trade_plan.is_entry_candle) at most this many
# candles after the MIN_TOUCHES-th touch.
MAX_AGE_BARS = 8
# Ranging: a zone with at least as many touches, ahead in the trade's direction and its
# middle within this share of price — the 4h rule (zone_touch_scan.py), the user's pick
# for the 1h too (27/10/26, USD/CAD: broke down out of a ×4 right onto a ×6). Half the
# 4h distance, as 1h moves are smaller.
RANGE_DISTANCE = 0.005
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
    candles: list[Candle] = field(default_factory=list, repr=False)  # for the alert's picture
    plan: Optional[TradePlan] = None  # entered on the last candle


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
    return setup_from(symbol, name, candles[-MAX_CANDLES:], daily_candles(df))


def setup_from(
    symbol: str, name: Optional[str], candles: Sequence[Candle], daily: Sequence[Candle]
) -> Optional[RetestSetup]:
    """The alert on the last of these closed 1h candles, if it's the entry after a
    setup's third touch; daily = the closed daily candles before it."""
    found = find_setup(candles)
    if found is None:
        return None
    zone, breakout, retests = found
    up = breakout.dir == "up"
    if not is_entry_candle(candles[-2], candles[-1], breakout.dir):
        return None
    leave = LEAVE * zone.unit
    after = candles[retests[-1] + 1 : -1]
    # Only pairs still sitting on the zone until the entry candle — once price has pulled
    # clear of it again after the last touch, the entry is gone (the user asked for these alone).
    if any(c.close > zone.top + leave if up else c.close < zone.bottom - leave for c in after):
        return None
    zones = detect_zones(candles, min_touches=ZONE_MIN_TOUCHES)
    if is_ranging(candles, zones, zone, breakout.dir, RANGE_DISTANCE):
        return None
    if not confirms(daily, breakout.dir):
        return None
    # The stop behind the zone and every touch's wick.
    trade = zone_plan(candles, retests[0], breakout.dir, zone.bottom, zone.top, zone.unit)
    if trade is None:
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
        candles=list(candles),
        plan=trade,
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


def format_message(s: RetestSetup) -> str:
    return telegram.alert_caption(s.symbol, s.name, s.direction == "up")


def send_alert(s: RetestSetup) -> bool:
    """The alert as a chart picture with the message as its caption — or the message
    alone if the picture can't be drawn. From the 1h alerts' own bot, if there is one;
    its trade is watched from then on (trade_tracker.py)."""
    try:
        png = alert_chart.render(
            s.candles, f"{s.name or s.symbol} · {TIMEFRAME}", s.zone_bottom, s.zone_top, s.zone_start_time,
            direction=s.direction, breakout=s.breakout_time, retests=s.retest_times, plan=s.plan,
        )
    except Exception:
        logger.exception("Retest alert: chart picture failed for %s, sending text only", s.symbol)
        png = None
    return trade_tracker.send_alert(png, format_message(s), "1h", s.key, s.symbol, s.name, s.plan)


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
                if send_alert(s):
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
