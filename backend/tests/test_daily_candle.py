import pandas as pd

from app.candles import Candle
from app.daily_candle import confirms, daily_candles
from app.zone_touch_scan import is_ranging, trade_direction
from app.zones import Zone


def days(*rows):
    return [Candle(time=i * 86400, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c) for i, (o, c) in enumerate(rows)]


def test_last_candle_in_the_trades_colour():
    assert confirms(days((100, 101), (101, 99)), "down")
    assert not confirms(days((100, 101), (101, 99)), "up")
    assert confirms(days((101, 99), (99, 100)), "up")


def test_older_candle_counts_while_later_ones_close_inside_its_body():
    # Red 105 → 100, then two days that close under its open: still a trade down.
    assert confirms(days((105, 100), (100, 103), (103, 104.5)), "down")
    # ...but one close above its open cancels it.
    assert not confirms(days((105, 100), (100, 106), (106, 107)), "down")


def test_wick_past_the_body_and_closes_further_down_dont_cancel_it():
    candles = days((105, 100), (100, 98))
    candles[1].high = 107  # wick above the red candle's open
    candles.append(Candle(time=2 * 86400, open=98, high=104, low=97, close=103))
    assert confirms(candles, "down")


def test_red_candle_more_than_three_days_back_is_too_old():
    assert not confirms(days((105, 100), (100, 101), (101, 102), (102, 103)), "down")


def test_green_candle_mirrors_red():
    assert confirms(days((100, 105), (105, 102), (102, 101)), "up")
    assert not confirms(days((100, 105), (105, 99), (99, 98)), "up")


def test_daily_candles_roll_over_at_5pm_new_york():
    # Two forex days from hourly bars: Mon 19/10 (from Sun 17:00 NY) and Tue 20/10.
    idx = pd.date_range("2026-10-18 17:00", "2026-10-20 16:00", freq="1h", tz="America/New_York")
    close = [100 + i * 0.1 for i in range(len(idx))]
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 0}, index=idx)
    out = daily_candles(df)
    assert len(out) == 2
    assert out[0].open == 100 and out[0].close == round(100 + 23 * 0.1, 5)
    assert out[1].open == round(100 + 24 * 0.1, 5)


def test_friday_after_the_close_stays_on_friday():
    idx = pd.date_range("2026-10-22 17:00", "2026-10-23 17:00", freq="1h", tz="America/New_York")
    df = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 0}, index=idx)
    assert len(daily_candles(df)) == 1


ZONE = Zone(id="z", bottom=100, top=102, unit=2, start_idx=0, touch_idx=[])


def bars(*closes):
    return [Candle(time=i * 3600, open=c, high=c, low=c, close=c) for i, c in enumerate(closes)]


def test_trade_direction_is_the_side_price_came_from():
    assert trade_direction(bars(98, 99, 101, 101.5), ZONE) == "down"  # up into resistance
    assert trade_direction(bars(104, 103, 101.5), ZONE) == "up"  # down onto support
    assert trade_direction(bars(104, 99.5), ZONE) == "down"  # a close just under the zone


def zone(bottom, top, touches):
    return Zone(id="z", bottom=bottom, top=top, unit=1, start_idx=0, touch_idx=list(range(touches)))


def test_ranging_only_when_the_zone_ahead_is_as_strong_and_close():
    resistance = zone(101, 102, 4)  # price at 101.5, a trade down
    candles = bars(101.5)
    assert is_ranging(candles, [resistance, zone(100.5, 100.8, 4)], resistance, "down", 0.01)
    assert not is_ranging(candles, [resistance, zone(100.5, 100.8, 3)], resistance, "down", 0.01)  # fewer touches
    assert not is_ranging(candles, [resistance, zone(98, 98.5, 6)], resistance, "down", 0.01)  # far away
    assert not is_ranging(candles, [resistance, zone(102.5, 103, 6)], resistance, "down", 0.01)  # behind the trade
