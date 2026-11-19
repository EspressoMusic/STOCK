from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import weekly_zone
from app.candles import Candle

NY = ZoneInfo("America/New_York")
MONDAY = int(datetime(2025, 1, 6, tzinfo=NY).timestamp())
WEEK = 7 * 86400

# Three dips to 100, each pushed back up to 112 — a support zone at 100 with 3 touches; then
# back down to it, two red weeks and a green one closing over the last one's body, still near
# the zone: the long's entry.
DIP = [112, 108, 104, 100.5, 104, 108]
CLOSES = [112, 112, 112] + DIP * 3 + [112, 108, 104, 101.5, 101, 102]
VISIT, ENTRY = len(CLOSES) - 3, len(CLOSES) - 1


def bars(closes, times):
    out = []
    for i, (c, t) in enumerate(zip(closes, times)):
        o = closes[i - 1] if i else c
        out.append(Candle(time=t, open=o, high=max(o, c) + 0.5, low=min(o, c) - 0.5, close=c, volume=1000))
    return out


def weeks(closes):
    return bars(closes, [MONDAY + i * WEEK for i in range(len(closes))])


def months(closes, year=2020):
    times = [int(datetime(year + i // 12, i % 12 + 1, 1, tzinfo=NY).timestamp()) for i in range(len(closes))]
    return bars(closes, times)


def k_with(n, entry_value):
    k = [50.0] * n
    k[-1] = entry_value
    return k


def test_long_on_the_week_closing_over_the_red_body_at_the_zone():
    candles = weeks(CLOSES)
    direction, zone, a, trade = weekly_zone.signal_at(candles, ENTRY)
    assert (direction, a) == ("up", VISIT)
    assert zone.bottom < 100 < zone.top and len(zone.touch_idx) >= 3
    assert trade.entry == 102
    assert trade.stop < 100  # under the zone and the visit's wicks
    assert trade.target == 102 + 3 * (102 - trade.stop)


def test_no_signal_once_price_ran_from_the_zone():
    assert weekly_zone._signal("ABC", None, weeks(CLOSES), ENTRY) is not None
    far = weeks([*CLOSES[:-1], 104.5])  # the same entry week, closing a typical week over the zone
    assert weekly_zone.signal_at(far, ENTRY) is not None and weekly_zone._signal("ABC", None, far, ENTRY) is None


def test_no_rule_on_the_rsi_but_the_monthly_zone_keeps_it():
    candles = weeks(CLOSES)
    assert weekly_zone.signal_at(candles, ENTRY) == weekly_zone.signal_at(candles, ENTRY, k_with(len(candles), 10))
    assert weekly_zone.signal_at(candles, ENTRY, k_with(len(candles), 50)) is None


def test_short_mirrors_it_at_resistance():
    candles = weeks([200 - c for c in CLOSES])
    direction, zone, a, trade = weekly_zone.signal_at(candles, ENTRY)
    assert (direction, a) == ("down", VISIT)
    assert trade.stop > 100
    assert weekly_zone.signal_at(candles, ENTRY, k_with(len(candles), 10)) is None


def test_a_week_is_closed_at_fridays_close():
    [c] = weeks([100])
    friday_close = datetime(2025, 1, 10, 16, tzinfo=NY).timestamp()
    assert not weekly_zone.week_closed(c, friday_close - 60)
    assert weekly_zone.week_closed(c, friday_close)


def signal():
    return weekly_zone._signal("ABC", None, weeks(CLOSES), ENTRY)  # the week of 30/6/25


def test_a_monthly_zone_of_three_touches():
    assert weekly_zone.monthly_zone(months(CLOSES), 99.5, 101) is not None
    assert weekly_zone.monthly_zone(months(CLOSES), 120, 121) is None


# Up to 150, then down 50 within six months: one strong monthly turn at the high.
TURN = [100, 110, 120, 130, 140, 150, 140, 130, 120, 110, 100, 100]


def test_a_monthly_zone_of_one_strong_turn(monkeypatch):
    bottom, top = weekly_zone.monthly_zone(months(TURN), 149, 152)
    assert bottom < 150.5 < top
    assert weekly_zone.monthly_zone(months(TURN), 124, 126) is None  # nowhere near the turn
    monkeypatch.setattr(weekly_zone, "STRONG_MOVE", 50.0)
    assert weekly_zone.monthly_zone(months(TURN), 149, 152) is None  # price didn't move far enough from it


def four_hours(closes, end):
    return bars(closes, [end - (len(closes) - i) * 14400 for i in range(len(closes))])


def test_the_4h_close_near_its_ema_50():
    flat = [100.0] * 200
    assert weekly_zone.near_ema_4h(four_hours([*flat, 100.5], 0))
    assert not weekly_zone.near_ema_4h(four_hours([*flat, 103], 0))
    assert not weekly_zone.near_ema_4h(four_hours([100.0] * 100, 0))  # the EMA hasn't settled


def test_confirmed_with_the_months_before_the_entry_weeks_and_its_4h():
    s = signal()
    end = s.time + WEEK
    h4 = four_hours([100.0] * 200 + [100.5], end - WEEK // 2) + four_hours([150.0], end + WEEK)  # the next week's left out
    assert weekly_zone.confirmed(s, months(CLOSES), h4) and s.month_zone[0] < 100 < s.month_zone[1]
    assert not weekly_zone.confirmed(signal(), months(CLOSES, year=2025)[6:], h4)  # from the entry week's month on
    assert not weekly_zone.confirmed(signal(), months(CLOSES), four_hours([100.0] * 200 + [110], end - WEEK // 2))


def days():
    start = datetime(2025, 3, 3, tzinfo=NY)
    times = [int((start + timedelta(days=d)).timestamp()) for d in range(180) if (start + timedelta(days=d)).weekday() < 5]
    return bars([100.0] * len(times), times)


def test_the_picture_is_the_weeks_beside_the_days_framed_with_no_gaps(monkeypatch):
    s = signal()
    all_days = days()
    monkeypatch.setattr(weekly_zone, "fetch_days", lambda symbol: all_days)
    charts, options = [], []
    monkeypatch.setattr(weekly_zone.alert_chart, "render_side_by_side", lambda *c, **kw: charts.extend(c) or options.append(kw) or b"png")
    weekly_zone.picture(s)
    (week_args, week_kw), (day_args, day_kw) = charts
    assert [week_args[1], day_args[1]] == ["ABC · 1W", "ABC · 1D"]
    week_days = [c for c in all_days if s.time <= c.time < s.time + WEEK]  # 30/6 to 4/7
    assert day_args[0][-1] is week_days[-1] and day_kw["after"] == week_kw["after"] == []  # ends at the alert
    assert day_kw["boxes"] == [(weekly_zone._iso(week_days[0].time), weekly_zone._iso(week_days[-1].time))]
    assert day_kw["breakout"] == weekly_zone._iso(week_days[-1].time) and week_kw["touches"] == s.touch_times
    assert all(day_kw[k] == week_kw[k] == v for k, v in weekly_zone.alert_chart.STYLE.items())  # plain: marks not drawn
    assert options == [dict(divider=True, frame=True)]


def test_the_weeks_alone_without_their_days(monkeypatch):
    monkeypatch.setattr(weekly_zone, "fetch_days", lambda symbol: [])
    charts, options = [], []
    monkeypatch.setattr(weekly_zone.alert_chart, "render_side_by_side", lambda *c, **kw: charts.extend(c) or options.append(kw) or b"png")
    weekly_zone.picture(signal())
    assert [args[1] for args, _ in charts] == ["ABC · 1W"]
    assert options == [dict(size=weekly_zone.WEEKS_SIZE, frame=True)]


def test_the_real_picture_draws(monkeypatch):
    monkeypatch.setattr(weekly_zone, "fetch_days", lambda symbol: days())
    assert weekly_zone.picture(signal()).startswith(b"\x89PNG")
