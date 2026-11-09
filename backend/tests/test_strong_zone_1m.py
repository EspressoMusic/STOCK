from types import SimpleNamespace

import pytest

from app import mt5_trader, strong_zone_1m
from app.candles import Candle
from app.config import settings
from app.strong_zone_1m import VISIT_BARS, find_entry, find_setup, visit_start, zones_at
from app.zones import Zone, typical_candle

ZONE = Zone(id="z", bottom=100, top=101, unit=1, start_idx=0, touch_idx=[0, 1, 2])
ABOVE = (103, 103.5, 102.5, 103)  # closed over 102: a typical candle over the zone
DOWN_ONTO_IT = (101.6, 101.7, 100.8, 101.2)
ENTRY_UP = (101.2, 101.8, 101, 101.7)
BELOW = (98, 98.5, 97.5, 98)  # closed under 99
UP_ONTO_IT = (99, 100.2, 98.9, 99.8)
ENTRY_DOWN = (99.8, 100, 98.7, 98.8)
FLAT = (101.2, 101.3, 101.1, 101.2)


def bars(*rows):
    return [Candle(time=i * 60, open=o, high=h, low=lo, close=c) for i, (o, h, lo, c) in enumerate(rows)]


def test_from_above_a_long_at_one_to_three_behind_the_visits_wicks():
    candles = bars(ABOVE, ABOVE, DOWN_ONTO_IT, ENTRY_UP)
    trade = find_entry(candles, [ZONE])
    unit = typical_candle(candles) * 101.7
    assert (trade.direction, trade.entry) == ("up", 101.7)
    assert trade.stop == pytest.approx(100.8 - 0.1 * unit)
    assert trade.target == pytest.approx(101.7 + 3 * (101.7 - trade.stop))


def test_from_below_a_short_at_one_to_three():
    candles = bars(BELOW, BELOW, UP_ONTO_IT, ENTRY_DOWN)
    trade = find_entry(candles, [ZONE])
    unit = typical_candle(candles) * 98.8
    assert (trade.direction, trade.entry) == ("down", 98.8)
    assert trade.stop == pytest.approx(100.2 + 0.1 * unit)
    assert trade.target == pytest.approx(98.8 - 3 * (trade.stop - 98.8))


def test_the_candle_that_reached_it_can_be_the_entry_even_closing_far_from_it():
    candles = bars(ABOVE, ABOVE, (103, 103.6, 100.9, 103.4))
    assert find_setup(candles, [ZONE]) == ("up", ZONE, 2)


def test_only_the_visits_first_entry_candle():
    assert find_entry(bars(ABOVE, ABOVE, DOWN_ONTO_IT, ENTRY_UP, (101.7, 101.95, 101.6, 101.9)), [ZONE]) is None


def test_the_entry_within_the_visits_candles_only():
    in_time = [ABOVE, ABOVE, DOWN_ONTO_IT, *[FLAT] * (VISIT_BARS - 2), ENTRY_UP]
    assert find_entry(bars(*in_time), [ZONE]) is not None
    assert find_entry(bars(ABOVE, ABOVE, DOWN_ONTO_IT, *[FLAT] * (VISIT_BARS - 1), ENTRY_UP), [ZONE]) is None


def test_a_close_past_the_far_side_ends_the_visit():
    assert find_entry(bars(ABOVE, ABOVE, DOWN_ONTO_IT, (101.2, 101.2, 99.5, 99.8), (99.8, 101, 99.7, 100.9)), [ZONE]) is None
    # A wick past it doesn't.
    assert find_entry(bars(ABOVE, ABOVE, DOWN_ONTO_IT, ENTRY_UP[:2] + (99.5, 101.7)), [ZONE]) is not None


def test_no_visit_without_a_close_away_first():
    assert visit_start(bars(DOWN_ONTO_IT, FLAT, ENTRY_UP), ZONE) is None
    # 101.9 is over the zone, but not a typical candle over it.
    assert find_entry(bars((101.9, 102, 101.8, 101.9), DOWN_ONTO_IT, ENTRY_UP), [ZONE]) is None


def test_no_visit_until_it_reaches_the_zone():
    assert visit_start(bars(ABOVE, ABOVE, (103, 103.5, 101.2, 103.4)), ZONE) is None


def test_leaving_again_makes_a_new_visit():
    candles = bars(ABOVE, DOWN_ONTO_IT, ENTRY_UP, ABOVE, DOWN_ONTO_IT, ENTRY_UP)
    assert find_setup(candles[:3], [ZONE]) == ("up", ZONE, 1)
    assert find_setup(candles, [ZONE]) == ("up", ZONE, 4)


def test_the_strongest_zone_first():
    other = Zone(id="other", bottom=100.5, top=101.5, unit=1, start_idx=0, touch_idx=[0, 1, 2])
    candles = bars(*[(104, 104.5, 103.5, 104)] * 2, DOWN_ONTO_IT, ENTRY_UP)
    assert find_setup(candles, [other, ZONE])[1] is other
    assert find_setup(candles, [ZONE, other])[1] is ZONE


def test_the_zones_from_the_days_closed_by_then(monkeypatch):
    day = strong_zone_1m.DAY_SECONDS
    rates = [{"time": i * day, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "tick_volume": 1} for i in range(4)]
    seen = {}
    monkeypatch.setattr(mt5_trader, "mt5", SimpleNamespace(TIMEFRAME_D1=16408, copy_rates_from_pos=lambda name, tf, pos, count: seen.update(tf=tf, count=count) or rates))
    monkeypatch.setattr(strong_zone_1m, "daily_zones", lambda days: [c.time for c in days])
    assert zones_at("EURUSD", 3 * day + 5) == [0, day, 2 * day]  # today's still forming
    assert seen == {"tf": 16408, "count": strong_zone_1m.DAILY_BARS}


def test_each_candle_is_looked_at_once_and_only_fresh(monkeypatch):
    candles = bars(ABOVE, ABOVE, DOWN_ONTO_IT, ENTRY_UP)
    placed = []
    monkeypatch.setattr(strong_zone_1m, "_last_bar", {})
    monkeypatch.setattr(strong_zone_1m, "closed_candles", lambda name, now: candles)
    monkeypatch.setattr(strong_zone_1m, "zones_at", lambda name, now: [ZONE])
    monkeypatch.setattr(mt5_trader, "place", lambda *args: placed.append(args) or 7)
    just_closed = candles[-1].time + 60 + 5
    assert strong_zone_1m._run_symbol("EURUSD", just_closed) == 1
    assert strong_zone_1m._run_symbol("EURUSD", just_closed + 20) == 0
    [(name, trade, magic, comment, reward_risk)] = placed
    assert (name, trade.direction, magic, comment, reward_risk) == ("EURUSD", "up", 30313, "BOT INVEST Zone 1m", 3)
    monkeypatch.setattr(strong_zone_1m, "_last_bar", {})
    assert strong_zone_1m._run_symbol("EURUSD", just_closed + 3600) == 0  # after a market break: stale
    assert len(placed) == 1


def test_a_refused_order_isnt_counted(monkeypatch):
    candles = bars(ABOVE, ABOVE, DOWN_ONTO_IT, ENTRY_UP)
    monkeypatch.setattr(strong_zone_1m, "_last_bar", {})
    monkeypatch.setattr(strong_zone_1m, "closed_candles", lambda name, now: candles)
    monkeypatch.setattr(strong_zone_1m, "zones_at", lambda name, now: [ZONE])
    monkeypatch.setattr(mt5_trader, "place", lambda *args: None)
    assert strong_zone_1m._run_symbol("XAUUSD", candles[-1].time + 65) == 0


def test_off_does_nothing(monkeypatch):
    monkeypatch.setattr(settings, "mt5_mode", "off")
    monkeypatch.setattr(mt5_trader, "connect", lambda: pytest.fail("connected"))
    assert strong_zone_1m.run_strategy() == 0
