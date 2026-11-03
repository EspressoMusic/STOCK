"""The touches that follow a breakout — the 1h alerts' rule ("אסטרטגיה 1",
retest_alerts.py), shared with the scans built on the same zones.

Zones and breakouts are the chart's own (zones.py)."""
from typing import Sequence

from .candles import Candle
from .zones import BREAK_BUFFER, RETEST_REACH, Breakout, Zone

# A price touched twice and turned from is a zone — what the chart window (trainer.html) draws.
ZONE_MIN_TOUCHES = 2
# A touch is a swing toward the zone: its high (low, after a breakout up) beats this
# many candles on each side. Price often hangs right under a broken zone and pokes it
# again and again (the user's GBP/CHF example, 21-22/09/26) — each poke is one touch.
TOUCH_BARS = 2
# Each touch has to come within this many candles of the one before it (the first, of
# the breakout). Price wandering back days later is just revisiting an old level, not
# retesting a breakout.
MAX_GAP_BARS = 30


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

