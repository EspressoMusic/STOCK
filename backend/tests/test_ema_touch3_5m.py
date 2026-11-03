from types import SimpleNamespace

import pytest

from app import ema_touch3_5m, mt5_trader
from app.candles import Candle
from app.config import settings
from app.ema_touch3_5m import find_entry, manage, reached_one_r, turned_against
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


def test_reached_one_r_from_the_trades_candle_on():
    window = bars((100, 100.5, 99.5, 100), (100, 101.2, 99.8, 101), (101, 101.5, 100.5, 101))
    # Risk 1 from 100: 1:1 is 101; the candle before the trade doesn't count.
    assert reached_one_r(window, "up", 100, 1, opened=300) == 1
    assert reached_one_r(window, "up", 100, 2, opened=300) is None
    assert reached_one_r(window, "down", 101, 1, opened=0) == 0


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
    monkeypatch.setattr(mt5_trader, "move_stop", lambda p, sl: moved.append((p.ticket, sl)))
    monkeypatch.setattr(ema_touch3_5m, "turned_against", lambda w, d, since: True)
    return moved


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
