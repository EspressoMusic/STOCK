from app import stock_day
from app.candles import Candle
from app.stock_day import DailySetup, Intraday, daily_setups, rejections, signal_at
from app.zones import Zone


def bars(*rows, start=0, step=86400):
    return [Candle(time=start + i * step, open=o, high=h, low=lo, close=c) for i, (o, h, lo, c) in enumerate(rows)]


def zone(*touches):
    return Zone(id="z", bottom=100, top=100.5, unit=1, start_idx=touches[0], touch_idx=list(touches))


UNDER = (97, 98, 96, 97)
TOUCH_TOP = (99, 100.8, 97.5, 98)  # up into the zone, closed red back under it
ABOVE = (104, 105, 103, 104)
TOUCH_BOTTOM = (102, 103.5, 100.2, 102.5)


def setups_with(monkeypatch, rows, *touches, side="down"):
    monkeypatch.setattr(stock_day, "side_zones", lambda window: [(side, zone(*touches))])
    return daily_setups(bars(*rows))


def test_a_zone_rejected_after_three_touches_is_a_short(monkeypatch):
    rows = [UNDER, TOUCH_TOP, UNDER, TOUCH_TOP, UNDER, TOUCH_TOP]
    found = setups_with(monkeypatch, rows, 1, 3)
    assert [(s.direction, s.touches) for s in found] == [("down", [1, 3, 5])]


def test_two_touches_are_not_enough(monkeypatch):
    assert setups_with(monkeypatch, [UNDER, TOUCH_TOP, UNDER, TOUCH_TOP], 1) == []


def test_the_rejection_may_be_the_day_before_yesterday(monkeypatch):
    rows = [UNDER, TOUCH_TOP, UNDER, TOUCH_TOP, UNDER, TOUCH_TOP, UNDER]
    assert setups_with(monkeypatch, rows, 1, 3)[0].touches == [1, 3, 5]


def test_a_rejection_older_than_that_does_not_count(monkeypatch):
    assert setups_with(monkeypatch, [UNDER, TOUCH_TOP, UNDER, TOUCH_TOP, UNDER, TOUCH_TOP, UNDER, UNDER], 1, 3, 5) == []


def test_coming_near_the_zone_is_no_touch(monkeypatch):
    rows = [UNDER, TOUCH_TOP, UNDER, TOUCH_TOP, UNDER, (99, 99.9, 97.5, 98)]
    assert setups_with(monkeypatch, rows, 1, 3) == []


def test_a_close_in_the_zone_is_no_rejection(monkeypatch):
    rows = [UNDER, TOUCH_TOP, UNDER, TOUCH_TOP, UNDER, (99, 101, 97, 100.2)]
    assert setups_with(monkeypatch, rows, 1, 3) == []


def test_a_green_candle_is_no_rejection_for_a_short(monkeypatch):
    rows = [UNDER, TOUCH_TOP, UNDER, TOUCH_TOP, UNDER, (97, 100.8, 96.5, 98)]
    assert setups_with(monkeypatch, rows, 1, 3) == []


def test_a_wide_zone_is_not_tight(monkeypatch):
    monkeypatch.setattr(
        stock_day, "side_zones",
        lambda window: [("down", Zone(id="z", bottom=100, top=101, unit=1, start_idx=1, touch_idx=[1, 3]))],
    )
    assert daily_setups(bars(UNDER, TOUCH_TOP, UNDER, TOUCH_TOP, UNDER, TOUCH_TOP)) == []


def test_a_close_far_through_the_zone_spoils_it(monkeypatch):
    rows = [UNDER, TOUCH_TOP, (98, 104, 97, 103), UNDER, TOUCH_TOP, UNDER, TOUCH_TOP]
    assert setups_with(monkeypatch, rows, 1, 4) == []


def test_the_long_mirrors_the_short(monkeypatch):
    rows = [ABOVE, TOUCH_BOTTOM, ABOVE, TOUCH_BOTTOM, ABOVE, TOUCH_BOTTOM]
    assert [s.direction for s in setups_with(monkeypatch, rows, 1, 3, side="up")] == ["up"]


# The 15m, the EMA flat at 100: a short's candles close under it.
UNDER_EMA = (98, 99, 97, 98)
WICK = (98, 100.5, 97.5, 98.5)
THROUGH = (98, 101, 97.5, 100.5)
EMA = [100.0] * 8


def test_wicks_at_the_ema_count_once_a_visit():
    m15 = bars(UNDER_EMA, WICK, UNDER_EMA, WICK, WICK, UNDER_EMA, UNDER_EMA, UNDER_EMA, step=900)
    assert rejections(m15, EMA, 7, "down") == [1, 4]


def test_a_close_through_the_ema_ends_the_count():
    m15 = bars(WICK, UNDER_EMA, THROUGH, UNDER_EMA, WICK, UNDER_EMA, UNDER_EMA, UNDER_EMA, step=900)
    assert rejections(m15, EMA, 7, "down") == [4]


def test_a_body_across_the_ema_is_no_wick():
    m15 = bars(UNDER_EMA, (100.5, 101, 98, 99), UNDER_EMA, step=900)
    assert rejections(m15, [100.0] * 3, 2, "down") == []


def data(h1_close=98.0, h4=(99, 100, 97, 98), m15_close=98.0, wicks=0, ema15=100.0, ema1h=100.0):
    """A signal's day: the 4h candle closed, the 1h and the last 15m candles closed under
    their EMAs and the zone."""
    start = 10 * 3600
    rows = [*[WICK, UNDER_EMA] * wicks, *[UNDER_EMA] * (6 - 2 * wicks), (98, 99, 97, m15_close)]
    m15 = bars(*rows, start=start, step=900)
    h1 = bars((98, 99, 97, h1_close), start=start - 3600, step=3600)
    four = bars(h4, start=start - 4 * 3600, step=4 * 3600)
    return Intraday(m15, [ema15] * len(m15), h1, [ema1h], [c.time for c in h1], four, [c.time for c in four])


DAILY = DailySetup("down", zone(1, 3, 5), [1, 3, 5], [])


def signal(**kw):
    d = data(**kw)
    return signal_at("X", DAILY, d, len(d.m15) - 1)


def test_all_the_rules_make_a_signal():
    s = signal()
    assert s.direction == "down" and not s.strong


def test_two_wicks_make_it_strong():
    assert signal(wicks=2).strong


def test_the_4h_candle_must_go_the_trades_way():
    assert signal(h4=(97, 100, 96, 98)) is None


def test_the_1h_must_be_under_its_ema():
    assert signal(ema1h=95.0) is None


def test_the_15m_must_be_under_its_ema():
    assert signal(ema15=95.0) is None


def test_the_15m_must_still_be_under_the_zone():
    assert signal(m15_close=100.5, ema15=102.0, ema1h=102.0) is None


def test_a_4h_candle_still_open_is_not_read():
    d = data()
    d.h4_times = [d.m15[-1].time]  # started with the last 15m candle
    assert signal_at("X", DAILY, d, len(d.m15) - 1) is None


def test_the_picture_gets_the_4h_up_to_the_closed_candle():
    d = data()
    in_progress = Candle(time=d.m15[-1].time, open=98, high=99, low=97, close=98)
    d.h4 = [*d.h4, in_progress]
    d.h4_times = [c.time for c in d.h4]
    assert signal_at("X", DAILY, d, len(d.m15) - 1).h4 == d.h4[:1]


def test_the_picture_has_the_three_timeframes(monkeypatch):
    charts = []
    monkeypatch.setattr(stock_day.alert_chart, "render_side_by_side", lambda *c, **kw: charts.extend(c) or b"png")
    s = signal(wicks=2)
    s.setup = DailySetup("down", DAILY.zone, [1, 3, 5], bars(*[UNDER, TOUCH_TOP] * 3))
    stock_day.picture(s)
    assert [args[1] for args, _ in charts] == ["X · 1D", "X · 4h", "X · 15m"]


def test_the_alert_opens_no_trade(monkeypatch):
    sent = []
    monkeypatch.setattr(stock_day, "picture", lambda s: b"png")
    monkeypatch.setattr(stock_day, "format_message", lambda s, example=False: "חזק EMA 50")
    monkeypatch.setattr(stock_day.trade_tracker, "send_alert", lambda *a: sent.append(a) or True)
    assert stock_day.send_alert(signal(wicks=2))
    png, text, bot, key, symbol, name, plan = sent[0]
    assert (bot, plan) == ("1h", None)


def test_plain_then_strong_but_not_strong_then_plain():
    plain, strong = signal(), signal(wicks=2)
    assert plain.key != strong.key
    assert plain.strong_key == strong.key
