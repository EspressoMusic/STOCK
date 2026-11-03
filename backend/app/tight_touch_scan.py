"""Stocks that came back to the same 4h zone a third time within a few days and turned
there — "סוג 2" on the stocks bot (the user's ask, 28/10/26). Type 1 (zone_touch_scan.py)
is a strong zone with many touches over weeks; this one is touches close together:

- price reached a zone and turned away from it at least twice in the last SPAN_BARS 4h
  candles (about five sessions: a stock has two a day), leaving it in between;
- now it's back a third time, and the entry candle (trade_plan.py) closes below the body
  of the one before it (came up into the zone from below → short) or above it (down into
  it from above → long). The alert comes with its trade: the stop behind the zone, 1:3.

The zones are the chart's own (zones.py) on fewer pivot bars, since touches this close
together are too near for the chart's 3-bar swings. Same daily-candle and ranging
filters as type 1. Checked after each of the session's two 4h candles closes (13:30 and
16:00 New York — the user's pick), and each signal is sent once (sent_alerts). Type 1's
alerts (zone_touch_scan.alert_setup) come from the same run, on the same candles — the
~25-minute universe is fetched once for both."""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from . import alert_chart, telegram, trade_tracker
from .candles import Candle, _to_candles, get_4h_dataframe
from .config import settings
from .daily_candle import confirms, daily_candles
from .technical_scan import _get_stock_universe, _stock_fetch_limiter
from .trade_plan import TradePlan, is_entry_candle, zone_plan
from .zone_touch_scan import (
    MIN_CANDLE_PCT,
    RANGE_DISTANCE,
    STOCK_EXCHANGES,
    STOCKS,
    WINDOW_BARS as TYPE1_WINDOW_BARS,
    ZoneTouchSetup,
    alert_setup,
    is_ranging,
    send_new,
    trade_direction,
)
from .zones import RETEST_REACH, Zone, detect_zones, typical_candle

logger = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
# 1h bars back this far: the zones' window, and the typical candle over as many as type 1.
HISTORY_PERIOD = "3mo"
# The two earlier touches come within this many 4h candles before the signal candle —
# five sessions, the longest of the user's "three, four or five days".
SPAN_BARS = 10
PIVOT_BARS = 2
# Zones come from this many candles (four weeks), so a level is found even when its two
# recent touches are its only ones.
WINDOW_BARS = 40
SESSION_CLOSE = (16, 0)  # New York
# ~17-23 signals a day on the whole universe (6 months of a 500-stock sample, 28/10/26),
# so about ten a run; past that, the most spread-out touches are left out.
MAX_ALERTS = 10
# The picture shows four weeks, so touches a few days apart don't shrink into its edge.
CHART_BARS = 40


@dataclass
class TightTouchSetup:
    symbol: str
    direction: str  # "down" = came up into the zone from below (short) | "up" = from above
    time: str  # the signal candle's (ISO)
    zone_bottom: float
    zone_top: float
    touch_times: list[str]  # the earlier touches, then this visit's first candle unless it's the signal candle
    candles: list[Candle] = field(default_factory=list, repr=False)  # for the alert's picture
    plan: Optional[TradePlan] = None  # entered on the signal candle, the last

    @property
    def key(self) -> str:
        return f"stocks2:{self.symbol}:{self.time}"


_run_lock = threading.Lock()


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def signal(window: Sequence[Candle]) -> Optional[tuple[str, Zone, int]]:
    """On the window's last candle: (direction, the zone with its earlier touches only,
    the third visit's first candle) — or None. The visit began on the last candle or the
    one before it, and the last candle is the entry (trade_plan.is_entry_candle) without
    closing through the zone."""
    n = len(window)
    if n < 3:
        return None
    c, p = window[-1], window[-2]
    first = n - 1 - SPAN_BARS
    zones = detect_zones(window, min_touches=2, pivot_bars=PIVOT_BARS)
    for zone in zones:  # strongest first
        reach = RETEST_REACH * zone.unit
        lo, hi = zone.bottom - reach, zone.top + reach

        def at_zone(x: Candle) -> bool:
            return x.low <= hi and x.high >= lo

        end = n - 1 if at_zone(c) else n - 2 if at_zone(p) else None
        if end is None:
            continue
        a = end
        while a > 0 and at_zone(window[a - 1]):
            a -= 1
        if a < n - 2:
            continue
        direction = trade_direction(window[:a], zone)
        if direction is None:
            continue
        down = direction == "down"
        if not is_entry_candle(p, c, direction) or (c.close > zone.top if down else c.close < zone.bottom):
            continue
        prior = [k for k in zone.touch_idx if first <= k < a]
        if len(prior) < 2:
            continue
        # Price left the zone, on the trade's side, after each touch.
        clear = (lambda x: x.high < lo) if down else (lambda x: x.low > hi)
        marks = prior + [a]
        if not all(any(clear(window[j]) for j in range(marks[m] + 1, marks[m + 1])) for m in range(len(marks) - 1)):
            continue
        # Ranging: only the zones' recent touches count, as for the touches themselves.
        recent = [replace(z, touch_idx=[k for k in z.touch_idx if k >= first]) for z in zones]
        zone = replace(zone, start_idx=prior[0], touch_idx=prior)
        if is_ranging(window, recent, zone, direction, RANGE_DISTANCE[STOCKS]):
            return None
        return direction, zone, a
    return None


def signal_at(candles: Sequence[Candle], i: int) -> Optional[tuple[str, Zone, int]]:
    """signal() on candle i, seeing only the WINDOW_BARS up to it; indexes are candles'."""
    lo = max(0, i + 1 - WINDOW_BARS)
    found = signal(candles[lo : i + 1])
    if not found:
        return None
    direction, zone, a = found
    return direction, replace(zone, start_idx=zone.start_idx + lo, touch_idx=[k + lo for k in zone.touch_idx]), a + lo


def closed(candles: list[Candle], now: datetime) -> list[Candle]:
    """Without the 4h candle still in progress — Yahoo includes the hour still trading.
    A session's first candle closes 4 hours in (13:30), its second at the close."""
    if not candles:
        return candles
    start = datetime.fromtimestamp(candles[-1].time, NY)
    end = min(start + timedelta(hours=4), start.replace(hour=SESSION_CLOSE[0], minute=SESSION_CLOSE[1], second=0))
    return candles if now >= end else candles[:-1]


def _type2(candles: list[Candle], daily: list[Candle], symbol: str) -> Optional[TightTouchSetup]:
    i = len(candles) - 1
    found = signal_at(candles, i)
    # A signal that already came on the candle before is the same visit.
    if not found or signal_at(candles, i - 1):
        return None
    direction, zone, a = found
    if not daily or not confirms(daily, direction):
        return None
    trade = zone_plan(candles, a, direction, zone.bottom, zone.top, zone.unit)
    if trade is None:
        return None
    return TightTouchSetup(
        symbol=symbol,
        direction=direction,
        time=_iso(candles[i].time),
        zone_bottom=zone.bottom,
        zone_top=zone.top,
        # The signal candle gets the arrow; a touch's dot there would cover it.
        touch_times=[_iso(candles[k].time) for k in [*zone.touch_idx, *([a] if a < i else [])]],
        candles=candles,
        plan=trade,
    )


def _evaluate(symbol: str, now: datetime) -> tuple[Optional[TightTouchSetup], Optional[ZoneTouchSetup]]:
    """(type 2's signal, type 1's) on the 4h candle that just closed."""
    _stock_fetch_limiter.wait()
    df = get_4h_dataframe(symbol, period=HISTORY_PERIOD)
    if df is None or df.empty:
        return None, None
    candles = closed(_to_candles(df), now)
    if len(candles) < WINDOW_BARS or typical_candle(candles[-100:]) < MIN_CANDLE_PCT:
        return None, None
    # Today's candles only: a stock that hasn't traded today has nothing new.
    if datetime.fromtimestamp(candles[-1].time, NY).date() != now.astimezone(NY).date():
        return None, None
    # Daily candles from the same bars: the day's grouping doesn't care if they're 1h or 4h.
    daily = daily_candles(df)
    return (
        _type2(candles, daily, symbol),
        alert_setup(symbol, None, candles[-TYPE1_WINDOW_BARS:], lambda: daily, STOCKS),
    )


def scan(now: Optional[datetime] = None) -> tuple[int, list[TightTouchSetup], list[ZoneTouchSetup]]:
    """(universe size, type 2's setups, type 1's) — the whole stocks universe."""
    now = now or datetime.now(timezone.utc)
    universe = _get_stock_universe(
        settings.stock_zone_touch_universe_size,
        min_volume=settings.stock_zone_touch_min_volume,
        exchanges=STOCK_EXCHANGES,
    )
    if not universe:
        logger.warning("Tight touch scan: empty stock universe (screener unavailable?)")
    results: list[TightTouchSetup] = []
    type1: list[ZoneTouchSetup] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate, sym, now) for sym, _ in universe]
        for fut in as_completed(futures):
            two, one = fut.result()
            if two is not None:
                results.append(two)
            if one is not None:
                type1.append(one)
    # Tightest first: the latest first touch — what's left out over MAX_ALERTS is the most spread out.
    results.sort(key=lambda s: s.touch_times[0], reverse=True)
    type1.sort(key=lambda s: -s.touch_count)
    return len(universe), results, type1


def format_message(s: TightTouchSetup) -> str:
    return telegram.alert_caption(s.symbol, None, s.direction == "up", tag="סוג 2")


def send_alert(s: TightTouchSetup) -> bool:
    """The chart picture with the message as its caption — or the message alone if the
    picture can't be drawn. From the stocks bot, as type 1; the trade is watched from then
    on (trade_tracker.py)."""
    try:
        png = alert_chart.render(
            s.candles, f"{s.symbol} · 4h · Type 2", s.zone_bottom, s.zone_top, s.touch_times[0],
            direction=s.direction, breakout=s.time, touches=s.touch_times, min_bars=CHART_BARS, plan=s.plan,
        )
    except Exception:
        logger.exception("Tight touch scan: chart picture failed for %s, sending text only", s.symbol)
        png = None
    return trade_tracker.send_alert(png, format_message(s), "stocks", s.key, s.symbol, None, s.plan)


def run_tight_touch_scan(db: Session) -> int:
    """Scans and sends type 2's new signals, up to MAX_ALERTS; returns how many went out.
    Type 1's aren't sent since 29/10/26 — the user's call: one strategy per bot."""
    with _run_lock:
        universe, found, type1 = scan()
        sent = send_new(db, found, send_alert, MAX_ALERTS)
        logger.info(
            "Stocks 4h scan: %d type 2 signals out of %d stocks, %d sent (%d type 1, not sent)",
            len(found), universe, sent, len(type1),
        )
        return sent
