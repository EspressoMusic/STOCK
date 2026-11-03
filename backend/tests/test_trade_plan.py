from app import zone_touch_scan
from app.candles import Candle
from app.trade_plan import behind, is_entry_candle, plan, zone_entry, zone_plan
from app.zones import Zone


def bars(*rows):
    """(open, high, low, close) rows, 4 hours apart."""
    return [Candle(time=i * 14400, open=o, high=h, low=l, close=c) for i, (o, h, l, c) in enumerate(rows)]


def test_entry_candle_is_in_the_trades_colour_and_closes_past_the_body():
    prev = Candle(0, open=100, high=101, low=98, close=99)
    assert is_entry_candle(prev, Candle(1, open=99.5, high=100, low=97, close=98.5), "down")
    assert not is_entry_candle(prev, Candle(1, open=99.5, high=100, low=98.8, close=99.2), "down")  # inside the body
    assert not is_entry_candle(prev, Candle(1, open=98, high=99, low=97.5, close=98.5), "down")  # green
    assert is_entry_candle(prev, Candle(1, open=99, high=101.5, low=98.5, close=101), "up")


def test_target_is_three_times_the_risk():
    short = plan("down", 100, 101)
    assert (short.stop, short.target) == (101, 97)
    assert plan("up", 100, 99).target == 103
    assert plan("down", 100, 99.5) is None  # a short's stop below the entry


def test_stop_goes_behind_the_zone_or_a_wick_past_it():
    candles = bars((99, 100.5, 98, 99.5), (99.5, 100, 96, 96.5))
    assert behind(candles, "down", 101, unit=1) == 101.1
    assert behind(candles, "down", 100, unit=1) == 100.6
    assert behind(candles, "up", 99, unit=1) == 95.9


# A zone at 100-101, one typical candle = 1 (reach 0.25 each side).
BELOW = (97, 98, 96, 97)
UP_TO_IT = (97, 100.5, 96.5, 99.5)
RED_UNDER_ITS_BODY = (99.5, 100, 96, 96.5)


def test_entry_after_coming_up_into_a_zone_is_a_short():
    candles = bars(BELOW, BELOW, UP_TO_IT, RED_UNDER_ITS_BODY)
    assert zone_entry(candles, 100, 101, 0.25) == ("down", 2)
    trade = zone_plan(candles, 2, "down", 100, 101, 1)
    assert (trade.entry, trade.stop) == (96.5, 101.1)
    assert trade.target == 96.5 - 3 * (101.1 - 96.5)


def test_no_entry_once_price_has_left_the_zone():
    candles = bars(BELOW, BELOW, UP_TO_IT, BELOW, (97.5, 98.5, 95, 95.5))
    assert zone_entry(candles, 100, 101, 0.25) is None


def test_zone_alert_needs_the_daily_candle_and_comes_with_its_trade(monkeypatch):
    zone = Zone(id="z", bottom=100, top=101, unit=1, start_idx=0, touch_idx=[0, 1, 2])
    monkeypatch.setattr(zone_touch_scan, "detect_zones", lambda *a, **k: [zone])
    candles = bars(*[BELOW] * 5, UP_TO_IT, RED_UNDER_ITS_BODY)
    red_day, green_day = [Candle(0, 10, 10, 9, 9)], [Candle(0, 9, 10, 9, 10)]
    s = zone_touch_scan.alert_setup("EURUSD=X", "EUR/USD", candles, lambda: red_day, zone_touch_scan.FOREX)
    assert (s.direction, s.time, s.plan.stop) == ("down", zone_touch_scan._iso(candles[-1].time), 101.1)
    assert s.touch_times[-1] == zone_touch_scan._iso(candles[5].time)  # this visit's first candle
    assert zone_touch_scan.alert_setup("EURUSD=X", "EUR/USD", candles, lambda: green_day, "forex") is None
