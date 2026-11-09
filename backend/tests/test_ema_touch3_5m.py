from types import SimpleNamespace

import pytest

from app import ema_touch3_5m, mt5_trader
from app.candles import Candle
from app.config import settings
from app.ema_touch3_5m import closed_through, find_entry, manage, open_gap_against, reached, turned_against
from app.zones import Zone


def bars(*rows):
    return [Candle(time=i * 300, open=o, high=h, low=lo, close=c) for i, (o, h, lo, c) in enumerate(rows)]


def zone(*touches):
    return Zone(id="z", bottom=100, top=101, unit=1, start_idx=touches[0], touch_idx=list(touches))


ABOVE = (103, 104, 102, 103)
DOWN_ONTO_IT = (102, 102.5, 100.8, 101.2)
ENTRY_UP = (101.2, 102.4, 101, 102.3)
# Came down onto the zone 100-101 (candle 4) and a green candle closes over its body.
LONG = bars(*[ABOVE] * 4, DOWN_ONTO_IT, ENTRY_UP)


def stub_setup(monkeypatch, direction="up"):
    monkeypatch.setattr(ema_touch3_5m, "find_setup", lambda w, ranging: (direction, zone(0, 2), 4))


def test_a_long_over_the_ema_at_one_to_two(monkeypatch):
    stub_setup(monkeypatch)
    trade = find_entry(LONG, ema=101.0)
    # The stop 0.1 candle under the zone's bottom (100), the target twice the risk.
    assert (trade.direction, trade.entry, trade.stop) == ("up", 102.3, pytest.approx(99.9))
    assert trade.target == pytest.approx(102.3 + 2 * 2.4)


def test_no_long_under_the_ema(monkeypatch):
    stub_setup(monkeypatch)
    assert find_entry(LONG, ema=103.0) is None


def test_no_short_over_the_ema(monkeypatch):
    stub_setup(monkeypatch, direction="down")
    assert find_entry(LONG, ema=101.0) is None


def test_the_touches_are_counted_without_the_ranging_check(monkeypatch):
    seen = {}
    monkeypatch.setattr(ema_touch3_5m, "find_setup", lambda w, ranging: seen.update(ranging=ranging))
    find_entry(LONG, ema=101.0)
    assert seen["ranging"] is False


def test_no_entry_while_a_gap_is_open_against_it(monkeypatch):
    stub_setup(monkeypatch)
    seen = {}
    monkeypatch.setattr(ema_touch3_5m, "open_gap_against", lambda w, d: seen.update(direction=d) or True)
    assert find_entry(LONG, ema=101.0) is None
    assert seen["direction"] == "up"


def test_no_entry_once_a_candle_closed_through_the_zone(monkeypatch):
    stub_setup(monkeypatch)
    # The visit's first candle closed a little under the zone's bottom (100).
    under = bars(*[ABOVE] * 4, (102, 102.5, 99.8, 99.95), ENTRY_UP)
    assert find_entry(under, ema=99.0) is None
    stub_setup(monkeypatch, direction="down")
    over = bars(*[(98, 99, 97, 98)] * 4, (99, 101.2, 98.8, 101.05), (99.8, 100, 98.5, 98.6))
    assert find_entry(over, ema=103.0) is None


def test_closed_through_counts_closes_not_wicks():
    assert not closed_through(bars((100.5, 101, 99.5, 100.2)), "up", 100, 101)
    assert closed_through(bars((100.5, 101, 99.5, 99.9)), "up", 100, 101)
    assert not closed_through(bars((100.5, 101.5, 100, 100.8)), "down", 100, 101)
    assert closed_through(bars((100.5, 101.5, 100, 101.1)), "down", 100, 101)


SMALL = (100, 100.3, 99.9, 100.1)  # bodies of 0.1
# A big red candle (a body of 1.9) and the one after it doesn't reach back up to the
# low before it (99.9): a gap 99.5-99.9.
DROP = bars(*[SMALL] * 21, (100, 100.2, 98, 98.1), (98.1, 99.5, 97.8, 99))
# The same up: a gap 100.3-100.5.
RISE = bars(*[SMALL] * 21, (100, 102, 99.9, 101.9), (101.9, 102.2, 100.5, 101))


def test_a_big_red_candles_gap_blocks_longs_until_closed_over():
    assert open_gap_against(DROP, "up")
    assert not open_gap_against(DROP, "down")
    assert open_gap_against(DROP + bars((99, 100.2, 98.9, 99.8)), "up")  # its wick only
    assert not open_gap_against(DROP + bars((99, 100.2, 98.9, 100)), "up")


def test_a_big_green_candles_gap_blocks_shorts():
    assert open_gap_against(RISE, "down")
    assert not open_gap_against(RISE, "up")
    assert not open_gap_against(RISE + bars((101, 101.2, 100, 100.2)), "down")


def test_no_gap_or_no_big_candle_blocks_nothing():
    no_gap = bars(*[SMALL] * 21, (100, 100.2, 98, 98.1), (98.1, 100, 97.8, 99))
    assert not open_gap_against(no_gap, "up")
    small = bars(*[SMALL] * 21, (100, 100.1, 99.7, 99.8), (99.8, 99.85, 99.6, 99.7))
    assert not open_gap_against(small, "up")


def test_reached_from_the_trades_candle_on():
    window = bars((100, 100.5, 99.5, 100), (100, 101.2, 99.8, 101), (101, 101.5, 100.5, 101))
    # 1 in profit from 100 is 101; the candle before the trade doesn't count.
    assert reached(window, "up", 100, 1, opened=300) == 1
    assert reached(window, "up", 100, 2, opened=300) is None
    assert reached(window, "down", 101, 1, opened=0) == 0


def test_turned_against_needs_a_third_touch_after_one_to_one(monkeypatch):
    window = bars(*[ABOVE] * 10)
    monkeypatch.setattr(ema_touch3_5m, "side_zones", lambda w: [("down", zone(2, 5, 8))])
    assert turned_against(window, "up", since=6)
    assert not turned_against(window, "up", since=9)  # its last touch came before 1:1
    assert not turned_against(window, "down", since=6)  # a short's zone against it is of lows
    monkeypatch.setattr(ema_touch3_5m, "side_zones", lambda w: [("down", zone(5, 8))])
    assert not turned_against(window, "up", since=6)  # two touches only


class FakeMT5:
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1

    def __init__(self, *positions):
        self.positions = list(positions)

    def positions_get(self, symbol=None):
        return self.positions


def position(**kw):
    p = dict(ticket=1, symbol="XAUUSD", magic=ema_touch3_5m.MAGIC, type=0, price_open=100.0, sl=99.0, tp=102.0, time=0)
    return SimpleNamespace(**{**p, **kw})


@pytest.fixture
def moved(monkeypatch):
    moved = []
    monkeypatch.setattr(mt5_trader, "move_stop", lambda p, sl: moved.append((p.ticket, sl)) or True)
    monkeypatch.setattr(ema_touch3_5m, "turned_against", lambda w, d, since: True)
    return moved


def test_stop_to_one_to_one_once_one_and_a_half_to_one(monkeypatch, moved):
    window = bars((100, 101.6, 99.9, 101.5))  # 1.5:1 = 101.5
    monkeypatch.setattr(mt5_trader, "mt5", FakeMT5(position()))
    manage("XAUUSD", window)
    assert moved == [(1, 101.0)]


def test_a_short_locks_one_to_one_too(monkeypatch, moved):
    window = bars((100, 100.1, 98.4, 98.6))
    monkeypatch.setattr(mt5_trader, "mt5", FakeMT5(position(type=1, sl=101.0, tp=98.0)))
    manage("XAUUSD", window)
    assert moved == [(1, 99.0)]


def test_from_break_even_on_to_one_to_one_once_only(monkeypatch, moved):
    window = bars((100, 101.6, 99.9, 101.5))
    monkeypatch.setattr(mt5_trader, "mt5", FakeMT5(position(sl=100.0), position(ticket=2, sl=101.0)))
    manage("XAUUSD", window)
    assert moved == [(1, 101.0)]


def test_back_under_one_to_one_break_even_instead(monkeypatch):
    moved = []
    monkeypatch.setattr(mt5_trader, "move_stop", lambda p, sl: moved.append(sl) or sl != 101.0)
    window = bars((100, 101.6, 99.9, 100.5))
    monkeypatch.setattr(mt5_trader, "mt5", FakeMT5(position()))
    manage("XAUUSD", window)
    assert moved == [101.0, 100.0]
    moved.clear()
    monkeypatch.setattr(mt5_trader, "mt5", FakeMT5(position(sl=100.0)))  # already there
    manage("XAUUSD", window)
    assert moved == [101.0]


def test_break_even_once_one_to_one_and_turned(monkeypatch, moved):
    window = bars((100, 101.1, 99.9, 101))  # 1:1 = 101 (risk 1 = target 2 away / 2)
    monkeypatch.setattr(mt5_trader, "mt5", FakeMT5(position()))
    manage("XAUUSD", window)
    assert moved == [(1, 100.0)]


def test_no_break_even_before_one_to_one(monkeypatch, moved):
    monkeypatch.setattr(mt5_trader, "mt5", FakeMT5(position()))
    manage("XAUUSD", bars((100, 100.9, 99.9, 100.5)))
    assert moved == []


def test_break_even_once_only_and_not_others_trades(monkeypatch, moved):
    window = bars((100, 101.1, 99.9, 101))
    monkeypatch.setattr(mt5_trader, "mt5", FakeMT5(position(sl=100.0), position(ticket=2, magic=mt5_trader.MAGIC)))
    manage("XAUUSD", window)
    assert moved == []


def test_a_short_moves_to_break_even_too(monkeypatch, moved):
    window = bars((100, 100.1, 98.9, 99))
    monkeypatch.setattr(mt5_trader, "mt5", FakeMT5(position(type=1, sl=101.0, tp=98.0)))
    manage("XAUUSD", window)
    assert moved == [(1, 100.0)]


def test_each_candle_is_looked_at_once_and_only_fresh(monkeypatch):
    candles = bars(*[ABOVE] * ema_touch3_5m.WINDOW_BARS)
    monkeypatch.setattr(ema_touch3_5m, "closed_candles", lambda name, now: candles)
    monkeypatch.setattr(ema_touch3_5m, "manage", lambda name, window: None)
    monkeypatch.setattr(ema_touch3_5m, "_last_bar", {})
    entries = []
    monkeypatch.setattr(ema_touch3_5m, "find_entry", lambda w, ema: entries.append(w[-1].time))
    just_closed = candles[-1].time + 300 + 5
    ema_touch3_5m._run_symbol("XAUUSD", just_closed)
    ema_touch3_5m._run_symbol("XAUUSD", just_closed + 60)
    assert entries == [candles[-1].time]
    monkeypatch.setattr(ema_touch3_5m, "_last_bar", {})
    ema_touch3_5m._run_symbol("XAUUSD", just_closed + 3600)  # after a market break: stale
    assert entries == [candles[-1].time]


def test_off_does_nothing(monkeypatch):
    monkeypatch.setattr(settings, "mt5_mode", "off")
    monkeypatch.setattr(mt5_trader, "connect", lambda: pytest.fail("connected"))
    assert ema_touch3_5m.run_strategy() == 0
