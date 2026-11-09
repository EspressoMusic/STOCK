from datetime import datetime
from zoneinfo import ZoneInfo

from matplotlib.figure import Figure

from app import alert_chart, misc_strategies as m
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


ORB_LONG = ((100, 101, 99, 100.5), (100.5, 101.5, 100, 101), (101, 101.2, 100.2, 100.8), (101.2, 102.5, 101, 102.2))


def sent(found, candles):
    """found as signals_at leaves it."""
    found.symbol, found.candles = "AAPL", candles
    found.rvol = 3.14
    return found


def test_orb_marks_the_range_and_the_breakout():
    candles = bars(*ORB_LONG)
    found = m.orb(m.build_series(candles), 3)
    assert [(mark.i, mark.price) for mark in found.marks] == [(1, 101.5), (3, 102.5)]
    assert "99–101.5" in found.marks[0].text


def test_sweep_marks_the_high_and_the_wick():
    rows = [(100, 101, 99.5, 100.5), (100.5, 103, 100.2, 102.5), (102.5, 102.8, 101, 101.5)]
    rows += [(101.5, 102, 100.8, 101.2)] * 4
    rows += [(103, 103.6, 102, 102.4)]
    found = m.liquidity_sweep(m.build_series(bars(*rows)), len(rows) - 1)
    assert [(mark.i, mark.price) for mark in found.marks] == [(1, 103), (7, 102)]
    assert found.stop_rule == "מעל הפתיל"


def test_mean_reversion_marks_the_stretched_low_and_the_turn():
    rows = [(100, 100.1, 99.9, 100)] * 25
    rows += [(100, 100, 98, 98), (97.3, 97.4, 97, 97.2), (97.2, 97.6, 97.1, 97.5)]
    found = m.mean_reversion(m.build_series(bars(*rows)), len(rows) - 1)
    assert [(mark.i, mark.price) for mark in found.marks] == [(26, 97), (27, 97.6)]
    assert "ATR מתחת ל-SMA 20 (מינימום 3)" in found.marks[0].text


def rising(n, first=100.0, step=0.2):
    return [(first + k * step, first + k * step + 0.25, first + k * step - 0.05, first + k * step + 0.2) for k in range(n)]


def test_trend_marks_where_the_two_hours_start_the_pullback_and_the_entry():
    rows = rising(80) + [(116, 116.05, 114, 115), (115, 116.6, 114.9, 116.5)]
    found = m.trend_following(m.build_series(bars(*rows)), 81)
    assert [mark.i for mark in found.marks] == [81 - m.TREND_BARS, 80, 81]
    assert found.marks[1].price == 114


def test_vwap_marks_the_hold_the_touch_and_the_entry():
    rows = rising(20) + [(104, 104, 102, 102.5), (102.5, 104.5, 102.4, 104.2)]
    found = m.vwap_pullback(m.build_series(bars(*rows)), 21)
    assert [mark.i for mark in found.marks] == [0, 20, 21]
    assert "VWAP והוא עולה (מינימום 80%)" in found.marks[0].text


def test_caption_explains_each_number_and_the_rules_not_on_the_picture():
    candles = bars(*ORB_LONG)
    text = m.format_message(sent(m.orb(m.build_series(candles), 3), candles))
    assert "1️⃣ טווח 15 הדקות הראשונות: 99–101.5" in text
    assert "2️⃣ הנר הראשון שסגר מעליו, ירוק" in text
    assert "מחזור פי 3.1 מהרגיל לשעה הזו (מינימום 2)" in text
    assert "סטופ באמצע הטווח · יעד 1:2" in text


def test_picture_numbers_the_marks_even_one_far_back():
    candles = bars(*[(100, 101, 99, 100)] * 200)
    ax = Figure().add_subplot()
    alert_chart.draw(ax, candles, "", None, None, None, numbers=[(m._iso(candles[100].time), 101), (m._iso(candles[-1].time), 99)], min_bars=30)
    assert [t.get_text() for t in ax.texts if t.get_text() in ("1", "2")] == ["1", "2"]
    assert ax.get_xlim()[1] > 40  # the view was pulled back to the first


def test_volume_panel_numbers_the_last_candle():
    candles = bars(*[(100, 101, 99, 100)] * 40)
    fig = Figure()
    ax = fig.add_subplot()
    alert_chart.draw(ax, candles, "", None, None, None, volume=(500, 3))
    panel = fig.axes[1]
    assert [t.get_text() for t in panel.texts if t.get_text() == "3"] == ["3"]
    assert panel.get_ylim()[1] > 1000  # the tallest bar fits
