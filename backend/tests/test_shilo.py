from datetime import datetime
from zoneinfo import ZoneInfo

from app import shilo
from app.candles import Candle

NY = ZoneInfo("America/New_York")
OPEN = int(datetime(2026, 11, 2, 9, 30, tzinfo=NY).timestamp())
DAY = 86400

# A swing low at 99 (candle 2), a close under it, a fall to 96.5 — then back up to 99 and
# a red candle closing under the body before it: the short's entry, on candle 9.
SHORT = [
    (100, 101, 99.5, 100.5), (100.5, 101, 99.8, 100.2), (100.2, 100.6, 99, 99.5), (99.5, 100.5, 99.4, 100.2),
    (100.2, 100.8, 99.6, 100.4), (100.4, 100.5, 98.5, 98.6), (98.6, 98.7, 96.5, 96.8), (96.8, 98, 96.6, 97.8),
    (97.8, 99.1, 97.7, 99), (99, 99.2, 97.5, 97.6),
]
ENTRY = 9


def bars(rows, start=OPEN, volume=1000):
    """(open, high, low, close) rows, 5 minutes apart from start."""
    return [Candle(time=start + i * 300, open=o, high=h, low=l, close=c, volume=volume) for i, (o, h, l, c) in enumerate(rows)]


def mirror(rows):
    return [(200 - o, 200 - l, 200 - h, 200 - c) for o, h, l, c in rows]


def setup(rows, direction, k_entry):
    candles = bars(rows)
    k = [50.0] * len(candles)
    k[ENTRY] = k_entry
    return shilo._setup(candles, k, shilo.typical(candles), [0] * len(candles), 2, direction)


def test_short_on_the_entry_candle_back_at_the_broken_low_when_overbought():
    assert setup(SHORT, "down", 90) == (ENTRY, 8)  # the visit from candle 8


def test_no_short_unless_the_stochastic_rsi_is_overbought():
    assert setup(SHORT, "down", 60) is None


def test_no_short_when_price_got_stuck_after_the_break():
    rows = list(SHORT)
    rows[6] = (98.6, 98.7, 98.3, 98.4)  # hardly past the low
    rows[7] = (98.4, 98.6, 98.2, 98.5)
    assert setup(rows, "down", 90) is None


def test_long_mirrors_it_when_oversold():
    assert setup(mirror(SHORT), "up", 10) == (ENTRY, 8)
    assert setup(mirror(SHORT), "up", 50) is None


def test_find_signals_with_the_trade_while_the_stock_is_in_play():
    quiet = [(100, 100.5, 99.5, 100)] * 20
    candles = bars(quiet, OPEN - 2 * DAY) + bars(quiet, OPEN - DAY) + bars(SHORT, volume=3000)
    k = [50.0] * len(candles)
    k[40 + ENTRY] = 90
    [found] = shilo.find_signals("MRVL", candles, k)
    assert (found.direction, found.i, found.level) == ("down", 40 + ENTRY, 99)
    assert found.plan.entry == 97.6
    assert found.plan.stop > 99.2  # over the visit's wicks
    assert found.plan.target == 97.6 - 3 * (found.plan.stop - 97.6)
    assert found.key == f"shilo:MRVL:down:{OPEN + ENTRY * 300}"


def test_find_signals_skips_a_stock_not_in_play():
    quiet = [(100, 100.5, 99.5, 100)] * 20
    candles = bars(quiet, OPEN - 2 * DAY) + bars(quiet, OPEN - DAY) + bars(SHORT)
    k = [50.0] * len(candles)
    k[40 + ENTRY] = 90
    assert shilo.find_signals("MRVL", candles, k) == []
