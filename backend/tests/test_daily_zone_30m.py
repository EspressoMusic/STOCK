import io

import matplotlib.image as mpimg
import pandas as pd

from app import alert_chart, daily_zone_30m, telegram, trade_tracker
from app.candles import Candle
from app.daily_zone_30m import ZoneSetup, day_so_far, find_at, four_hours, is_break, touched
from app.zones import Zone


def bars(*rows):
    return [Candle(time=i * 1800, open=o, high=h, low=l, close=c) for i, (o, h, l, c) in enumerate(rows)]


# A daily zone at 100-101, price came up to it from below (a short); the EMA at 99.
ZONE = Zone(id="z", bottom=100, top=101, unit=1, start_idx=0, touch_idx=[0, 1, 2])
EMA = 99.0
BEFORE = (97.0, 97.5, 96.5, 97.2)
UP_TO_THE_EMA = (98.0, 99.2, 97.9, 98.8)
GREEN = (98.8, 100.2, 98.7, 99.8)
RED_UNDER_IT = (99.8, 99.9, 98.0, 98.2)  # under the green body and the EMA


def short(*rows, ema=EMA):
    m = bars(BEFORE, *rows)
    return m, [ema] * len(m)


def test_a_touch_then_a_red_candle_under_the_green_body_and_the_ema():
    m, emas = short(UP_TO_THE_EMA, GREEN, RED_UNDER_IT)
    found = find_at(m, emas, [ZONE], 3, 0.5)
    assert found.direction == "down" and found.entry == 3 and found.visit == 2
    assert found.touch == 2  # the green candle, the latest touch before the break


def test_a_long_is_the_mirror():
    m = bars(
        (104.0, 104.5, 103.5, 103.8), (103.0, 103.1, 101.8, 102.2), (102.2, 102.3, 100.8, 101.2),
        (101.2, 103.0, 101.1, 102.8),
    )
    assert is_break(m, [102.0] * 4, 3, "up")
    assert find_at(m, [102.0] * 4, [ZONE], 3, 0.5).direction == "up"


def test_the_candle_before_must_be_the_opposite_colour():
    m, emas = short(UP_TO_THE_EMA, (100.0, 100.2, 99.0, 99.4), (99.4, 99.5, 98.0, 98.2))
    assert not is_break(m, emas, 3, "down")


def test_closing_only_inside_the_opposite_body_is_no_break():
    m, emas = short(UP_TO_THE_EMA, GREEN, (99.8, 99.9, 98.5, 98.9))  # the green body starts at 98.8
    assert not is_break(m, emas, 3, "down")


def test_the_close_must_be_past_the_ema():
    m, emas = short(UP_TO_THE_EMA, GREEN, RED_UNDER_IT, ema=97.0)
    assert not is_break(m, emas, 3, "down")


def test_with_no_ema_touch_there_is_no_signal():
    m = bars(BEFORE, (98.0, 98.5, 97.9, 98.4), (98.4, 100.2, 98.3, 99.8), (99.8, 99.9, 98.0, 98.2))
    assert not is_break(m, [105.0] * 4, 3, "down")


def test_a_touch_too_long_ago_doesnt_count():
    far = (98.0, 99.2, 97.9, 98.8)  # touches the 99 EMA, 3 candles before the break
    clear = (98.0, 98.5, 97.9, 98.4)
    m = bars(BEFORE, far, clear, (98.4, 98.9, 98.3, 98.7), (98.7, 98.8, 97.0, 97.5))
    assert not is_break(m, [99.0] * 5, 4, "down")


def test_a_crossing_counts_though_no_candle_was_on_the_ema():
    m = bars((98.0, 98.5, 97.5, 98.2), (99.5, 100.0, 99.4, 99.8))
    assert touched(m, [99.0, 99.0], 1)
    assert not touched(m, [97.0, 97.0], 1)


def test_only_the_visits_first_break():
    m, emas = short(UP_TO_THE_EMA, GREEN, RED_UNDER_IT, (98.2, 100.1, 98.1, 99.9), (99.9, 100.0, 97.0, 97.5))
    assert is_break(m, emas, 3, "down") and find_at(m, emas, [ZONE], 3, 0.5) is not None
    assert is_break(m, emas, 5, "down") and find_at(m, emas, [ZONE], 5, 0.5) is None


def test_a_close_through_the_zone_is_no_signal():
    m, emas = short((98.0, 102.0, 97.9, 101.6), (101.6, 103.0, 101.5, 102.5), (102.5, 102.6, 101.4, 101.5), ema=102.0)
    assert is_break(m, emas, 3, "down")  # red, under the green body and the EMA, on the EMA
    assert find_at(m, emas, [ZONE], 3, 0.5) is None  # but it closed over the zone


def test_away_from_the_zones_there_is_no_signal():
    m, emas = short(UP_TO_THE_EMA, GREEN, RED_UNDER_IT)
    assert is_break(m, emas, 3, "down")
    assert find_at(m, emas, [Zone(id="z", bottom=110, top=111, unit=1, start_idx=0, touch_idx=[0])], 3, 0.5) is None


def test_the_day_so_far_starts_at_the_forex_rollover():
    # 21:30 UTC is 16:30 in New York, the day before's last candle; 22:00 is 17:00, the new day's first.
    rows = [(1, 9, 0.5, 1.5), (1.5, 4, 1.4, 3), (3, 3.5, 2, 2.5)]
    m = [Candle(77400 + i * 1800, o, h, l, c) for i, (o, h, l, c) in enumerate(rows)]
    c = day_so_far(m)
    assert (c.time, c.open, c.high, c.low, c.close) == (79200, 1.5, 4, 1.4, 2.5)


def test_four_hours_from_half_hours():
    idx = pd.date_range("2026-11-03 00:00", periods=16, freq="30min", tz="UTC")
    df = pd.DataFrame(
        {"Open": range(16), "High": [x + 1 for x in range(16)], "Low": range(16), "Close": range(16), "Volume": [1] * 16},
        index=idx,
    )
    out = four_hours(df)
    assert [(c.open, c.high, c.close, c.volume) for c in out] == [(0, 8, 7, 8), (8, 16, 15, 8)]


def setup():
    m = bars(*[BEFORE] * 10, UP_TO_THE_EMA, GREEN, RED_UNDER_IT)
    return ZoneSetup(
        symbol="EURUSD=X", name="EUR/USD", direction="down", zone_bottom=100, zone_top=101, touch_count=3,
        touch_time=alert_chart.datetime.fromtimestamp(m[-2].time, alert_chart.timezone.utc).isoformat(),
        time=alert_chart.datetime.fromtimestamp(m[-1].time, alert_chart.timezone.utc).isoformat(), key="k",
        candles_1d=m, candles_4h=m, candles_30m=m, emas=[EMA] * len(m),
    )


def test_the_alert_is_three_charts_with_no_trade_drawn_or_watched(monkeypatch):
    charts, options, tracked, opened = [], [], [], []

    def side_by_side(*c, **kw):
        charts.extend(c)
        options.append(kw)
        return b"png"

    monkeypatch.setattr(daily_zone_30m.alert_chart, "render_side_by_side", side_by_side)
    monkeypatch.setattr(telegram, "post_photo", lambda png, text, chat_id=None, bot_token=None: 7)
    monkeypatch.setattr(trade_tracker, "track", lambda *args: tracked.append(args))
    monkeypatch.setattr(trade_tracker.mt5_trader, "open_trade", lambda *args: opened.append(args))
    assert daily_zone_30m.send_alert(setup())
    assert [args[1] for args, _ in charts] == ["EUR/USD · 1D", "EUR/USD · 4h", "EUR/USD · 30m · EMA 50"]
    assert all(kwargs.get("plan") is None for _, kwargs in charts)
    assert charts[2][1]["line"] and options == [dict(size=daily_zone_30m.PICTURE_SIZE, divider=True)]
    assert tracked == [] and opened == []


def _black_columns(png: bytes) -> list[int]:
    image = mpimg.imread(io.BytesIO(png))[:, :, :3]
    return [x for x in range(image.shape[1]) if (image[:, x].max(axis=1) < 0.1).all()]


def test_the_picture_has_two_thick_black_lines():
    black = _black_columns(daily_zone_30m.render(setup()))
    groups = sum(1 for a, b in zip([-9, *black], black) if b - a > 1)
    assert groups == 2 and len(black) >= 2 * 4


def test_other_side_by_side_pictures_have_no_line():
    chart = (setup().candles_30m, "x", None, None, None), {}
    assert _black_columns(alert_chart.render_side_by_side(chart, chart)) == []
