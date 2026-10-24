"""Price zones the market turned at several times, and the breakouts through them.

A line-for-line port of frontend/src/lib/zones.js (ZONES_ALGO_VERSION 3), so a zone
the backend alerts on is the same zone the chart draws. Keep the two in step: change
a rule here only together with the JS, and vice versa.

Sizes are in "typical candles": the median candle range as a fraction of price, times
the price where it's applied."""
from dataclasses import dataclass
from typing import Optional, Sequence

from .candles import Candle

PIVOT_BARS = 3  # a swing high/low must beat this many bars on each side
TOLERANCE = 0.5  # pivots within ±this many typical candles of a zone's centre are touches of it
MIN_TOUCHES = 3  # the chart's default "min. touches" setting
MIN_HEIGHT = 0.3  # thinnest zone drawn
BREAK_BUFFER = 0.1  # a close must clear the zone by this much to count as a breakout
RETEST_REACH = 0.25  # how near a pullback must come back to the zone to count as a retest
LEAVE = 1  # how far past the zone price must close after a breakout before a pullback counts
FAST_RETEST_BARS = 3  # a retest this soon after the breakout needn't wait for LEAVE (see retest_alerts)
CONFIRM_BARS = 5  # how soon a quick retest has to be followed through
MAX_ZONES = 16


@dataclass
class Zone:
    id: str
    bottom: float
    top: float
    unit: float  # one typical candle at this zone's price
    start_idx: int
    touch_idx: list[int]

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class Breakout:
    idx: int
    dir: str  # "up" | "down"


def typical_candle(candles: Sequence[Candle]) -> float:
    """Median true range as a fraction of the close — robust to the odd gap or news bar."""
    ranges = []
    for i, c in enumerate(candles):
        prev_close = candles[i - 1].close if i > 0 else c.open
        r = (max(c.high, prev_close) - min(c.low, prev_close)) / c.close
        if r > 0:
            ranges.append(r)
    ranges.sort()
    return ranges[len(ranges) // 2] if ranges else 0.0


def _find_pivots(candles: Sequence[Candle], pivot_bars: int) -> list[tuple[int, float]]:
    """(index, price) of swing highs and lows. Ties count for the earlier bar only, so a
    flat double top is one pivot."""
    pivots = []
    for i in range(pivot_bars, len(candles) - pivot_bars):
        is_high = is_low = True
        for k in range(1, pivot_bars + 1):
            if candles[i - k].high >= candles[i].high or candles[i + k].high > candles[i].high:
                is_high = False
            if candles[i - k].low <= candles[i].low or candles[i + k].low < candles[i].low:
                is_low = False
        if is_high:
            pivots.append((i, candles[i].high))
        if is_low:
            pivots.append((i, candles[i].low))
    return pivots


def _cluster_zones(candles: Sequence[Candle], pivots, candle: float, min_touches: int, tolerance: float) -> list[Zone]:
    """Repeatedly takes the price with the most pivots around it as a zone, then removes
    those pivots, until no price has min_touches left. Highs and lows mix on purpose:
    old resistance that turns into support is the same zone."""
    remaining = pivots
    zones: list[Zone] = []
    while len(remaining) >= min_touches and len(zones) < MAX_ZONES:
        best = None
        for _, price in remaining:
            reach = tolerance * candle * price
            members = [q for q in remaining if abs(q[1] - price) <= reach]
            if best is None or len(members) > len(best):
                best = members
        if len(best) < min_touches:
            break

        bottom = min(p for _, p in best)
        top = max(p for _, p in best)
        unit = candle * ((top + bottom) / 2)
        if top - bottom < MIN_HEIGHT * unit:
            mid = (top + bottom) / 2
            bottom = mid - MIN_HEIGHT * unit / 2
            top = mid + MIN_HEIGHT * unit / 2
        exclusion = tolerance * unit / 2
        remaining = [p for p in remaining if p[1] < bottom - exclusion or p[1] > top + exclusion]
        if any(bottom <= z.top and top >= z.bottom for z in zones):
            continue

        touch_idx = sorted(i for i, _ in best)
        zones.append(
            Zone(
                id=f"{candles[touch_idx[0]].time}:{bottom:.6g}",
                bottom=bottom,
                top=top,
                unit=unit,
                start_idx=touch_idx[0],
                touch_idx=touch_idx,
            )
        )
    return zones


def detect_zones(
    candles: Sequence[Candle], min_touches: int = MIN_TOUCHES, pivot_bars: int = PIVOT_BARS, tolerance: float = TOLERANCE
) -> list[Zone]:
    """Zones strongest first, as detectZones() in zones.js returns them."""
    if len(candles) < pivot_bars * 2 + 1:
        return []
    candle = typical_candle(candles)
    if not candle > 0:
        return []
    return _cluster_zones(candles, _find_pivots(candles, pivot_bars), candle, min_touches, tolerance)


def find_breakouts(candles: Sequence[Candle], zone: Zone) -> list[Breakout]:
    """A breakout is a close past the zone on the opposite side from the previous close
    outside it, once the zone already has at least two touches — the same candles
    zones.js tries a retest from."""
    buffer = BREAK_BUFFER * zone.unit
    out: list[Breakout] = []
    side: Optional[str] = None
    touches_before = 0
    for i in range(zone.start_idx, len(candles)):
        while touches_before < len(zone.touch_idx) and zone.touch_idx[touches_before] < i:
            touches_before += 1
        c = candles[i]
        now = "above" if c.close > zone.top + buffer else "below" if c.close < zone.bottom - buffer else None
        if now and side and now != side and touches_before >= 2:
            out.append(Breakout(idx=i, dir="up" if now == "above" else "down"))
        if now:
            side = now
    return out
