from datetime import datetime
from zoneinfo import ZoneInfo

from app import misc_strategies as m
from app.candles import Candle

NY = ZoneInfo("America/New_York")
OPEN = int(datetime(2026, 11, 2, 9, 30, tzinfo=NY).timestamp())


def bars(*rows, start=OPEN, volume=1000):
    """(open, high, low, close) rows, 5 minutes apart from the open."""
    return [Candle(time=start + i * 300, open=o, high=h, low=l, close=c, volume=volume) for i, (o, h, l, c) in enumerate(rows)]


def test_orb_long_on_the_first_green_close_over_the_opening_high():
    candles = bars((100, 101, 99, 100.5), (100.5, 101.5, 100, 101), (101, 101.2, 100.2, 100.8),
                   (100.8, 101.4, 100.6, 101.2), (101.2, 102.5, 101, 102.2))
    s = m.build_series(candles)
    assert m.orb(s, 3) is None  # closed inside the range
    found = m.orb(s, 4)
    assert found.direction == "up"
    assert found.zone == (99, 101.5)
    assert found.plan.stop == 100.25  # the range's middle
    assert found.plan.target == 102.2 + 2 * (102.2 - 100.25)


def test_orb_only_the_first_breakout_of_the_day():
    candles = bars((100, 101, 99, 100.5), (100.5, 101.5, 100, 101), (101, 101.2, 100.2, 100.8),
                   (100.8, 102, 100.6, 101.8), (101.8, 101.9, 101, 101.2), (101.2, 102.5, 101, 102.2))
    s = m.build_series(candles)
    assert m.orb(s, 3).direction == "up"
    assert m.orb(s, 5) is None


def test_orb_short_needs_a_red_close_under_the_low():
    candles = bars((100, 101, 99, 100.5), (100.5, 101.5, 100, 101), (101, 101.2, 100.2, 100.8),
                   (98, 99, 97.5, 98.5))  # green, though under the low
    assert m.orb(m.build_series(candles), 3) is None


def test_sweep_short_when_a_red_candle_wicks_over_the_high_and_closes_back_under():
    rows = [(100, 101, 99.5, 100.5), (100.5, 103, 100.2, 102.5), (102.5, 102.8, 101, 101.5)]
    rows += [(101.5, 102, 100.8, 101.2)] * 4
    rows += [(103, 103.6, 102, 102.4)]  # over 103, back under it, red
    found = m.liquidity_sweep(m.build_series(bars(*rows)), len(rows) - 1)
    assert found.direction == "down"
    assert found.level == 103
    assert found.plan.stop > 103.6


def test_sweep_closing_over_the_high_is_no_signal():
    rows = [(100, 101, 99.5, 100.5), (100.5, 103, 100.2, 102.5), (102.5, 102.8, 101, 101.5)]
    rows += [(101.5, 102, 100.8, 101.2)] * 4
    rows += [(103.5, 104, 102.8, 103.2)]
    assert m.liquidity_sweep(m.build_series(bars(*rows)), len(rows) - 1) is None


def test_rvol_against_the_same_time_on_earlier_days():
    day = 86400
    candles = (bars((1, 1, 1, 1), (1, 1, 1, 1), start=OPEN - 2 * day, volume=100)
               + bars((1, 1, 1, 1), (1, 1, 1, 1), start=OPEN - day, volume=100)
               + bars((1, 1, 1, 1), (1, 1, 1, 1), volume=300))
    s = m.build_series(candles)
    assert s.rvol[0] != s.rvol[0]  # NaN: no earlier day to compare with
    assert s.rvol[-1] == 3.0


def test_no_signal_while_not_in_play():
    candles = bars((100, 101, 99, 100.5), (100.5, 101.5, 100, 101), (101, 101.2, 100.2, 100.8),
                   (101.2, 102.5, 101, 102.2))
    assert m.signals_at(m.build_series(candles), 3, "X") == []  # no earlier days: volume unknown


def test_key_is_once_a_day_per_stock_strategy_and_side():
    candles = bars((100, 101, 99, 100.5), (100.5, 101.5, 100, 101), (101, 101.2, 100.2, 100.8),
                   (101.2, 102.5, 101, 102.2))
    found = m.orb(m.build_series(candles), 3)
    found.symbol, found.candles = "AAPL", candles
    assert found.key == "misc:orb:AAPL:up:2026-11-02"
