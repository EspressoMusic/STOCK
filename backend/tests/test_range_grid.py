from types import SimpleNamespace

import pytest

from app import mt5_trader, range_grid
from app.candles import Candle
from app.range_grid import RANGE_DAYS, Grid, crossed, grid_of, taken

from test_mt5_trader import fake  # noqa: F401 — the fixture: EURUSD at 1.1000/1.1001

# Steps of 0.001 from 1.0960 to 1.1060: the buys at 1.0960-1.1000, the middle 1.1010, the
# sells at 1.1020-1.1060; EURUSD's fake quote, 1.1000/1.1001, on the top buy level.
GRID = Grid(1.0960, 1.1060)


def day(i, high, low):
    return Candle(time=i * 86400, open=low, high=high, low=low, close=high)


def minute(o, h, lo, c):
    return Candle(time=0, open=o, high=h, low=lo, close=c)


def position(buy, tp):
    return SimpleNamespace(type=0 if buy else 1, tp=tp, magic=range_grid.MAGIC, symbol="EURUSD")


def test_the_range_of_the_last_days():
    days = [day(0, 2.0, 0.5)] + [day(i, 1.1060 - i * 0.0001, 1.0960 + i * 0.0001) for i in range(RANGE_DAYS)]
    grid = grid_of(days)
    assert (grid.low, grid.high) == (1.0960, 1.1060)  # the first, oldest day left out
    assert grid_of(days[: RANGE_DAYS - 1]) is None


def test_buys_under_the_middle_sells_over_it():
    assert GRID.levels("up") == pytest.approx([1.0960, 1.0970, 1.0980, 1.0990, 1.1000])
    assert GRID.levels("down") == pytest.approx([1.1020, 1.1030, 1.1040, 1.1050, 1.1060])
    assert GRID.side_steps == 10  # 5 trades, 2 steps each


def test_a_step_to_the_target_two_to_the_stop():
    long, short = GRID.plan("up", 1.0980), GRID.plan("down", 1.1040)
    assert (long.stop, long.target) == pytest.approx((1.0960, 1.0990))
    assert (short.stop, short.target) == pytest.approx((1.1060, 1.1030))


def test_a_buy_level_on_the_way_down_a_sell_level_on_the_way_up():
    down = crossed(GRID, 1.0995, minute(1.0995, 1.0996, 1.0978, 1.0985))
    assert [(d, round(lv, 4)) for d, lv in down] == [("up", 1.098), ("up", 1.099)]
    up = crossed(GRID, 1.1015, minute(1.1015, 1.1031, 1.1014, 1.1028))
    assert [(d, round(lv, 4)) for d, lv in up] == [("down", 1.102), ("down", 1.103)]
    # Up through a buy level, or down through a sell one: nothing.
    assert crossed(GRID, 1.0985, minute(1.0985, 1.0995, 1.0984, 1.0994)) == []
    assert crossed(GRID, 1.1035, minute(1.1035, 1.1036, 1.1025, 1.1026)) == []


def test_one_trade_a_level():
    plan = GRID.plan("up", 1.0990)
    assert taken(GRID, plan, [position(True, 1.1000)])
    assert taken(GRID, plan, [position(True, 1.1003)])  # an older day's level, under half a step off
    assert not taken(GRID, plan, [position(True, 1.1010)])
    assert not taken(GRID, plan, [position(False, 1.1000)])


def test_sized_by_a_whole_sides_risk(fake):
    assert range_grid._open("EURUSD", GRID, GRID.plan("up", 1.1000), [], 10_000)
    (req,) = fake.sent
    # 5% of 10,000 = 500; a lot loses 10 steps * 0.001 * 100,000 = 1,000 a side.
    assert req["volume"] == 0.5 and req["type"] == fake.ORDER_TYPE_BUY and req["price"] == 1.1001
    assert (req["sl"], req["tp"]) == (1.098, 1.101) and req["magic"] == range_grid.MAGIC


def test_the_smallest_lot_when_the_risk_is_too_small_for_it(fake):
    range_grid._open("EURUSD", GRID, GRID.plan("up", 1.1000), [], 100)
    assert fake.sent[0]["volume"] == 0.01


def test_not_past_the_target(fake):
    # A short at 1.1020, its target 1.1010: the bid, 1.1000, is past it.
    assert not range_grid._open("EURUSD", GRID, GRID.plan("down", 1.1020), [], 10_000)
    assert fake.sent == []
