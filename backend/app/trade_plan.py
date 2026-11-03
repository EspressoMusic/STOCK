"""The trade every alert comes with — the user's rules for all the bots (28/10/26):

- in on the close of a candle in the trade's colour that closes past the body of the
  one before it: a red one under its body for a short, a green one over it for a long
  (is_entry_candle) — no alert without one;
- the stop just behind the zone price came to, and behind the wicks of the visit if
  they reached further (for the EMA model, behind the candle on the line);
- the target three times the risk.

alert_chart.py draws it on the alert's picture, as TradingView's long/short position tool.
Penny stocks have no trade (penny_scan.py is a watchlist), so no plan."""
from dataclasses import dataclass
from typing import Optional, Sequence

from .candles import Candle

REWARD_RISK = 3
# How far past the zone (or the wicks) the stop goes, in typical candles.
STOP_BUFFER = 0.1


@dataclass
class TradePlan:
    direction: str  # "up" = long | "down" = short
    entry: float
    stop: float
    target: float


def is_entry_candle(prev: Candle, c: Candle, direction: str) -> bool:
    """c is in the trade's colour and closed past prev's body the trade's way."""
    if direction == "down":
        return c.close < c.open and c.close < min(prev.open, prev.close)
    return c.close > c.open and c.close > max(prev.open, prev.close)


def plan(direction: str, entry: float, stop: float, reward_risk: float = REWARD_RISK) -> Optional[TradePlan]:
    """The target reward_risk times the risk away; None if the stop isn't on the losing side."""
    risk = stop - entry if direction == "down" else entry - stop
    if risk <= 0:
        return None
    target = entry - reward_risk * risk if direction == "down" else entry + reward_risk * risk
    return TradePlan(direction, entry, stop, target)


def first_hit(trade: TradePlan, bars: Sequence[Candle]) -> Optional[tuple[str, int]]:
    """("target" or "stop", the bar's time) — whichever of the trade's levels the bars
    reached first. A bar that reached both counts as the stop: which came first inside it
    isn't known."""
    down = trade.direction == "down"
    for b in bars:
        if b.high >= trade.stop if down else b.low <= trade.stop:
            return "stop", b.time
        if b.low <= trade.target if down else b.high >= trade.target:
            return "target", b.time
    return None


def behind(candles: Sequence[Candle], direction: str, level: float, unit: float) -> float:
    """The stop: STOP_BUFFER typical candles (unit, in price) past level or the candles'
    wicks, whichever reach further — above them for a short, below for a long."""
    if direction == "down":
        return max([level, *(c.high for c in candles)]) + STOP_BUFFER * unit
    return min([level, *(c.low for c in candles)]) - STOP_BUFFER * unit


def zone_plan(
    candles: Sequence[Candle], start: int, direction: str, bottom: float, top: float, unit: float
) -> Optional[TradePlan]:
    """In on the last candle's close, the stop behind the zone's far edge and the candles
    from start (the visit) on."""
    level = top if direction == "down" else bottom
    return plan(direction, candles[-1].close, behind(candles[start:], direction, level, unit))


def zone_entry(candles: Sequence[Candle], bottom: float, top: float, reach: float) -> Optional[tuple[str, int]]:
    """(direction, the visit's first candle) when the last candle is the entry at this zone:
    price came to it (within reach) on that candle or the one before — from below, a
    short at resistance ("down"); from above, a long ("up") — and the last candle is the
    visit's first entry candle, not closing through the zone."""
    n = len(candles)
    if n < 3:
        return None
    lo, hi = bottom - reach, top + reach

    def at_zone(x: Candle) -> bool:
        return x.low <= hi and x.high >= lo

    end = n - 1 if at_zone(candles[-1]) else n - 2 if at_zone(candles[-2]) else None
    if end is None:
        return None
    a = end
    while a > 0 and at_zone(candles[a - 1]):
        a -= 1
    if a == 0:
        return None
    direction = "up" if candles[a - 1].low > hi else "down"
    c = candles[-1]
    if not is_entry_candle(candles[-2], c, direction) or (c.close > top if direction == "down" else c.close < bottom):
        return None
    if any(is_entry_candle(candles[k - 1], candles[k], direction) for k in range(a, n - 1)):
        return None
    return direction, a
