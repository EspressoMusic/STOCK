from app import daily_zone_4h, telegram, trade_tracker
from app.candles import Candle
from app.daily_zone_4h import Confirm, DailyZoneSetup, Found, confirmation, trade_for
from app.trade_plan import plan
from app.zones import Zone


def hours(*rows):
    return [Candle(time=i * 3600, open=o, high=h, low=l, close=c) for i, (o, h, l, c) in enumerate(rows)]


# A daily zone at 100-101, price came down onto it (a long); one typical 1h candle = 1.
ZONE = Zone(id="z", bottom=100, top=101, unit=1, start_idx=0, touch_idx=[0, 1, 2])
VISIT = [(101.5, 101.8, 100.4, 100.6), (100.6, 101.0, 100.2, 100.9)]


def test_a_big_candle_the_trades_way():
    h1 = hours(*VISIT, (100.9, 101.8, 100.6, 101.6), (101.6, 103.5, 101.5, 103.3))
    assert confirmation(h1, 0, 2, ZONE, "up", 1) == Confirm("big", 3)


def test_a_small_candle_is_no_confirmation():
    h1 = hours(*VISIT, (100.9, 101.8, 100.6, 101.6), (101.6, 102.5, 101.5, 102.3))
    assert confirmation(h1, 0, 2, ZONE, "up", 1) is None


def test_a_close_through_the_zone_ends_it():
    h1 = hours(*VISIT, (100.9, 101.0, 99.3, 99.5), (99.5, 101.5, 99.4, 101.3))
    assert confirmation(h1, 0, 2, ZONE, "up", 1) is None


# A swing high at 102 (candle 2), a close over it, a pullback onto it and a green entry candle.
RETEST = [
    *VISIT,
    (100.9, 102.0, 100.8, 101.8),  # 2 the swing
    (101.8, 101.9, 101.0, 101.2),
    (101.2, 101.6, 100.9, 101.4),
    (101.4, 102.6, 101.3, 102.4),  # 5 the breakout
    (102.4, 102.5, 102.1, 102.2),  # 6 back at the swing
    (102.2, 103.0, 102.1, 102.9),  # 7 the entry
]


def test_a_breakout_and_a_retest():
    assert confirmation(hours(*RETEST), 0, 2, ZONE, "up", 1) == Confirm("retest", 7, 102.0, 2, 6)


def test_back_under_the_swing_is_no_breakout():
    failed = [*RETEST[:6], (102.4, 102.5, 101.5, 101.7), RETEST[7]]
    assert confirmation(hours(*failed), 0, 2, ZONE, "up", 1) is None


def test_a_short_is_the_mirror():
    flip = [(-o, -l, -h, -c) for o, h, l, c in RETEST]
    zone = Zone(id="z", bottom=-101, top=-100, unit=1, start_idx=0, touch_idx=[0, 1, 2])
    assert confirmation(hours(*flip), 0, 2, zone, "down", 1) == Confirm("retest", 7, -102.0, 2, 6)


def test_a_strong_setups_stop_is_behind_the_zone_the_rests_behind_the_wicks():
    h1 = hours(*VISIT, (100.9, 101.8, 100.6, 101.6), (101.6, 103.5, 101.5, 103.3))
    f = Found(ZONE, "up", 0, 0, 0, Confirm("big", 3), 1)
    assert round(trade_for(h1, f, strong=True).stop, 6) == 99.9  # the zone's bottom, 100
    assert round(trade_for(h1, f, strong=False).stop, 6) == 100.1  # the lowest wick, 100.2


def test_the_alert_marks_the_zones_with_no_trade_drawn_or_watched(monkeypatch):
    h1 = hours(*VISIT, (100.9, 101.8, 100.6, 101.6), (101.6, 103.5, 101.5, 103.3))
    setup = DailyZoneSetup(
        symbol="EURUSD=X", name="EUR/USD", direction="up", zone_bottom=100, zone_top=101, touch_count=3,
        turn_time="1970-01-01T00:00:00+00:00", time="1970-01-01T03:00:00+00:00", key="k", kind="big",
        candles_4h=h1[:1], candles_1h=h1, plan=plan("up", 103.3, 100),
    )
    charts, tracked, opened = [], [], []
    monkeypatch.setattr(daily_zone_4h.alert_chart, "render_side_by_side", lambda *c: charts.extend(c) or b"png")
    monkeypatch.setattr(telegram, "post_photo", lambda png, text, chat_id=None, bot_token=None: 7)
    monkeypatch.setattr(trade_tracker, "track", lambda *args: tracked.append(args))
    monkeypatch.setattr(trade_tracker.mt5_trader, "open_trade", lambda *args: opened.append(args))
    assert daily_zone_4h.send_alert(setup)
    assert len(charts) == 2 and all(kwargs.get("plan") is None for _, kwargs in charts)
    assert tracked == [] and opened == []
