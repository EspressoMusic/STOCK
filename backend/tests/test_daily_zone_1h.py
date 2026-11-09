import pandas as pd

from app import daily_zone_1h, telegram, trade_tracker
from app.candles import Candle
from app.daily_zone_1h import (
    EMA_SETTLE, MAX_WAIT, ZoneSetup, ema_crosses, find_setups, format_message, four_hour_agrees, strong_zones,
    walk,
)
from app.daily_zone_4h import daily_zones
from app.zones import Zone

HOUR = 3600
QUARTER = 900
DAY = 86400


def bars(*rows):
    return [Candle(time=i * HOUR, open=o, high=h, low=l, close=c) for i, (o, h, l, c) in enumerate(rows)]


def zone(bottom, top):
    return Zone(id="z", bottom=bottom, top=top, unit=1, start_idx=0, touch_idx=[0, 1, 2])


def mirror(rows, around):
    return [(around - o, around - l, around - h, around - c) for o, h, l, c in rows]


# The user's AUD/USD picture, 4-5/11/26: 05:00 Israel time on, the zone as the bot finds it.
AUDUSD_ZONE = (0.6992, 0.70254)
AUDUSD = [
    (0.69706, 0.69706, 0.69589, 0.69589), (0.69589, 0.69745, 0.69589, 0.69730), (0.69735, 0.69857, 0.69735, 0.69852),
    (0.69867, 0.69876, 0.69808, 0.69876), (0.69881, 0.70008, 0.69857, 0.69969), (0.69984, 0.70082, 0.69984, 0.70053),
    (0.70038, 0.70180, 0.70038, 0.70175), (0.70180, 0.70240, 0.70126, 0.70146), (0.70136, 0.70146, 0.70102, 0.70146),
    (0.70146, 0.70175, 0.70111, 0.70151), (0.70156, 0.70220, 0.70156, 0.70200), (0.70185, 0.70225, 0.70166, 0.70180),
    (0.70151, 0.70200, 0.70087, 0.70151), (0.70146, 0.70146, 0.70048, 0.70131), (0.70131, 0.70205, 0.70116, 0.70185),
    (0.70190, 0.70190, 0.70087, 0.70092), (0.70092, 0.70136, 0.70082, 0.70111), (0.70111, 0.70126, 0.70097, 0.70111),
    (0.70107, 0.70107, 0.70082, 0.70087), (0.70072, 0.70087, 0.70053, 0.70053), (0.70062, 0.70087, 0.70053, 0.70062),
    (0.70077, 0.70200, 0.70077, 0.70180), (0.70161, 0.70230, 0.70126, 0.70175), (0.70180, 0.70230, 0.70175, 0.70195),
    (0.70190, 0.70230, 0.70156, 0.70180), (0.70185, 0.70185, 0.70141, 0.70156), (0.70156, 0.70166, 0.70097, 0.70107),
    (0.70107, 0.70230, 0.70102, 0.70230), (0.70249, 0.70269, 0.70151, 0.70171), (0.70180, 0.70230, 0.70151, 0.70200),
    (0.70200, 0.70205, 0.70107, 0.70146), (0.70141, 0.70161, 0.70126, 0.70141), (0.70166, 0.70180, 0.70151, 0.70175),
    (0.70171, 0.70171, 0.70003, 0.70043),
]
# The hours a 15m candle closed under the 50 EMA, from Yahoo's 15m bars.
AUDUSD_CROSSES = (18, 26, 30, 31, 33)
# The user's EUR/USD pictures, 4-5/11/26: 01:00 Israel time on. The one of 8/11/26 marks the two
# breaks, 17:00 and 09:00.
EURUSD_ZONE = (1.12397, 1.1279)
EURUSD = [
    (1.11894, 1.12108, 1.11894, 1.12108), (1.12108, 1.12271, 1.12032, 1.12183), (1.12196, 1.12638, 1.12196, 1.12435),
    (1.12435, 1.12613, 1.12322, 1.12562), (1.12587, 1.12587, 1.12397, 1.12473), (1.12486, 1.12625, 1.12372, 1.12372),
    (1.12360, 1.12423, 1.12296, 1.12397), (1.12397, 1.12410, 1.12296, 1.12334), (1.12334, 1.12473, 1.12271, 1.12423),
    (1.12423, 1.12613, 1.12410, 1.12511), (1.12511, 1.12651, 1.12473, 1.12613), (1.12625, 1.12663, 1.12486, 1.12511),
    (1.12511, 1.12600, 1.12410, 1.12575), (1.12575, 1.12714, 1.12562, 1.12663), (1.12663, 1.12752, 1.12625, 1.12625),
    (1.12625, 1.12689, 1.12511, 1.12537), (1.12524, 1.12524, 1.12360, 1.12372), (1.12372, 1.12397, 1.12171, 1.12208),
    (1.12221, 1.12423, 1.12208, 1.12397), (1.12385, 1.12423, 1.12183, 1.12208), (1.12233, 1.12271, 1.12208, 1.12259),
    (1.12259, 1.12347, 1.12259, 1.12334), (1.12322, 1.12347, 1.12309, 1.12322), (1.12271, 1.12385, 1.12271, 1.12322),
    (1.12347, 1.12397, 1.12347, 1.12385), (1.12397, 1.12511, 1.12385, 1.12511), (1.12511, 1.12600, 1.12486, 1.12600),
    (1.12587, 1.12600, 1.12537, 1.12575), (1.12587, 1.12689, 1.12562, 1.12689), (1.12689, 1.12714, 1.12638, 1.12663),
    (1.12663, 1.12663, 1.12613, 1.12638), (1.12638, 1.12701, 1.12625, 1.12701), (1.12689, 1.12689, 1.12448, 1.12448),
]
EURUSD_CROSSES = (16, 23, 32)

# A daily zone at 100-101, price comes up to it from below: a short.
BEFORE = (97.5, 97.6, 96.5, 97.0)
ARRIVE = (97.2, 100.2, 97.1, 100.0)
PULLBACK = (100.0, 100.2, 99.4, 99.6)
NEW_HIGH = (99.6, 100.4, 99.5, 100.3)  # the structure: it started at 99.6, went to 100.3
BREAK = (100.3, 100.4, 99.0, 99.2)
AWAY = (99.2, 99.3, 98.8, 99.0)
BACK = (99.0, 100.5, 98.9, 100.4)
DIP = (100.4, 100.5, 100.1, 100.2)
PUSH = (100.2, 100.7, 100.1, 100.6)  # the structure again: it started at 100.2
ALERT = (100.6, 100.7, 99.9, 100.0)  # red, the 15m crossing the EMA during it
SETUP = [BEFORE, ARRIVE, PULLBACK, NEW_HIGH, BREAK, AWAY, BACK, DIP, PUSH, ALERT]
ZONE = zone(100, 101)
RED_4H = [Candle(time=4 * HOUR, open=100.3, high=100.4, low=98.8, close=99.0)]


def found(rows, h4=RED_4H, at=ZONE, reach=0.5, crossed=None):
    """crossed: the hours the 15m crossed the EMA during, both ways; None = every one."""
    hours = {i * HOUR for i in (range(len(rows)) if crossed is None else crossed)}
    return walk(bars(*rows), h4, {"down": hours, "up": hours}, at, 0, len(rows), reach)


def test_the_users_audusd_example():
    h4 = [Candle(time=13 * HOUR, open=0.70146, high=0.70205, low=0.70048, close=0.70111)]
    f, = found(AUDUSD, h4, zone(*AUDUSD_ZONE), 0.00035, AUDUSD_CROSSES)
    assert f.direction == "down"
    assert (f.visit, f.broke, f.back, f.entry) == (4, 13, 27, 30)  # 09:00, 18:00, 08:00, 11:00


def test_the_users_eurusd_example():
    h4 = [Candle(time=13 * HOUR, open=1.12575, high=1.12752, low=1.12360, close=1.12372)]
    f, = found(EURUSD, h4, zone(*EURUSD_ZONE), 0.0005, EURUSD_CROSSES)
    assert f.direction == "down"
    assert (f.visit, f.broke, f.back, f.entry) == (2, 16, 28, 32)  # 03:00, 17:00, 05:00, 09:00 (the user's line)


def test_a_long_is_the_mirror():
    around = 1.4
    bottom, top = AUDUSD_ZONE
    h4 = [Candle(time=13 * HOUR, open=around - 0.70146, high=around - 0.70048, low=around - 0.70205, close=around - 0.70111)]
    f, = found(mirror(AUDUSD, around), h4, zone(around - top, around - bottom), 0.00035, AUDUSD_CROSSES)
    assert f.direction == "up"
    assert (f.visit, f.broke, f.back, f.entry) == (4, 13, 27, 30)


def test_break_return_then_a_red_candle_with_the_15m_crossing():
    f, = found(SETUP, crossed=[9])
    assert (f.visit, f.broke, f.back, f.entry) == (1, 4, 6, 9)
    f, = found(SETUP)
    assert f.entry == 7  # DIP, the first red candle after the return


def test_no_15m_crossing_no_signal():
    assert found(SETUP, crossed=[]) == []
    assert found(SETUP, crossed=[8]) == []  # PUSH, green
    assert found(SETUP, crossed=[5]) == []  # before the return


def test_a_run_from_far_off_the_zone_is_no_structure():
    far = (99.4, 100.4, 99.3, 100.3)  # NEW_HIGH from 99.4, a little more than half a candle off the zone
    assert found([BEFORE, ARRIVE, PULLBACK, far, *SETUP[4:]]) == []


def test_off_the_zone_another_return_is_waited_for():
    off = (99.4, 99.45, 98.0, 98.2)  # red, but under the zone's reach
    assert found([*SETUP[:7], (100.4, 100.5, 99.2, 99.4), off], crossed=[8]) == []
    f, = found([*SETUP[:7], off, BACK, ALERT], crossed=[7, 9])
    assert (f.back, f.entry) == (8, 9)


def test_a_wick_under_the_structure_is_no_break():
    wick = (100.3, 100.4, 99.0, 99.8)  # the structure started at 99.6
    assert found([BEFORE, ARRIVE, PULLBACK, NEW_HIGH, wick, (99.8, 100.4, 99.7, 100.4), ALERT]) == []


def test_a_green_run_under_the_high_is_only_a_pullback():
    down = (100.0, 100.2, 99.0, 99.2)
    no_new_high = (99.2, 99.8, 99.1, 99.7)
    under_it = (99.7, 99.8, 98.9, 99.0)  # under no_new_high's start, but that isn't the structure
    assert found([BEFORE, ARRIVE, down, no_new_high, under_it, BACK, ALERT]) == []


def test_without_the_return_there_is_no_signal():
    assert found([*SETUP[:6], (99.0, 99.4, 98.8, 99.3), (99.3, 99.4, 98.9, 99.0)]) == []


def test_a_return_only_to_the_break_is_not_back_at_the_top():
    assert found([*SETUP[:6], (99.0, 99.9, 98.9, 99.8), (99.8, 99.9, 99.2, 99.3)]) == []


def test_a_return_too_late_is_no_signal():
    waits = [(99.0, 99.1, 98.9, 99.0)] * (MAX_WAIT + 1)
    assert found([*SETUP[:6], *waits, *SETUP[6:]]) == []


def test_a_close_through_the_zone_ends_it():
    through = (99.0, 101.8, 98.9, 101.5)
    assert found([*SETUP[:6], through, *SETUP[6:]]) == []


def test_once_a_visit():
    assert len(found([*SETUP, *SETUP[6:]])) == 1


def test_no_4h_candle_the_trades_way_no_signal():
    assert found(SETUP, h4=[]) == []
    green = [Candle(time=4 * HOUR, open=99.0, high=100.4, low=98.8, close=100.3)]
    assert found(SETUP, h4=green) == []


def test_the_4h_candle_must_be_closed_and_of_this_visit():
    red = RED_4H[0]
    assert four_hour_agrees([red], HOUR, 8 * HOUR, "down")
    assert not four_hour_agrees([red], HOUR, 7 * HOUR, "down")  # still trading
    assert not four_hour_agrees([red], 8 * HOUR, 12 * HOUR, "down")  # closed before the visit


def test_a_touch_needs_only_one_candle_on_each_side():
    highs = [98.0, 100.0, 98.0, 100.2, 98.0, 99.8, 98.0, 98.0]
    days = [Candle(time=i * DAY, open=97.6, high=h, low=97.5, close=97.8) for i, h in enumerate(highs)]
    assert daily_zones(days) == []
    z, = [z for z in strong_zones(days) if z.top > 99]
    assert len(z.touch_idx) == 3


def test_find_setups_takes_a_candle_once_and_from_since_on(monkeypatch):
    monkeypatch.setattr(daily_zone_1h, "strong_zones", lambda days: [ZONE, ZONE] if len(days) else [])
    monkeypatch.setattr(daily_zone_1h, "ema_crosses", lambda q: {"down": {9 * HOUR}, "up": set()})
    m = bars(*SETUP)
    days = [Candle(-3 * DAY, 97, 98, 96, 97), Candle(-2 * DAY, 97, 98, 96, 97), Candle(-DAY + 1800, 97, 98, 96, 97)]
    f, = find_setups(m, RED_4H, [], days)
    assert f.entry == 9
    assert f.turns == [d.time for d in days]  # the daily candles of the zone's touches
    assert len(find_setups(m, RED_4H, [], days, 9 * HOUR)) == 1
    assert find_setups(m, RED_4H, [], days, 10 * HOUR) == []


def test_the_15m_crosses_the_ema_in_the_hour_of_its_candle():
    closes = [100.0] * EMA_SETTLE + [99.0, 101.0, 101.5, 100.5]
    q = [Candle(time=i * QUARTER, open=c, high=c, low=c, close=c) for i, c in enumerate(closes)]
    hour = EMA_SETTLE * QUARTER - EMA_SETTLE * QUARTER % HOUR
    crosses = ema_crosses(q)
    assert crosses["down"] == {hour}  # 100.5 is still over the EMA
    assert crosses["up"] == {hour}  # 101 back over it, the same hour


def test_a_cross_before_the_ema_settles_doesnt_count():
    closes = [100.0, 99.0, 101.0] + [101.0] * EMA_SETTLE
    q = [Candle(time=i * QUARTER, open=c, high=c, low=c, close=c) for i, c in enumerate(closes)]
    assert ema_crosses(q) == {"down": set(), "up": set()}


def test_an_empty_answer_from_yahoo_is_asked_again(monkeypatch):
    frame = pd.DataFrame({"Open": [1.0]})
    answers = iter([pd.DataFrame(), None, frame])

    class Ticker:
        def __init__(self, symbol):
            pass

        def history(self, interval, period):
            answer = next(answers)
            if answer is None:
                raise ValueError("no answer")
            return answer

    monkeypatch.setattr(daily_zone_1h.yf, "Ticker", Ticker)
    monkeypatch.setattr(daily_zone_1h, "FETCH_PAUSE_SECONDS", 0)
    assert daily_zone_1h._fetch("EURUSD=X") is frame
    answers = iter([pd.DataFrame()] * daily_zone_1h.FETCH_TRIES)
    assert daily_zone_1h._fetch("EURUSD=X") is None


def setup():
    m = bars(*[BEFORE] * 10, *SETUP)
    return ZoneSetup(
        symbol="EURUSD=X", name="EUR/USD", direction="down", zone_bottom=100, zone_top=101, touch_count=3,
        time=daily_zone_1h._iso(m[-1].time), key="k", candles_4h=m, candles_1h=m,
        broke=daily_zone_1h._iso(m[-4].time), candles_1d=m, turns=[daily_zone_1h._iso(m[2].time)],
    )


def test_the_alert_is_three_charts_with_no_trade_drawn_or_watched(monkeypatch):
    charts, options, tracked, opened = [], [], [], []

    def side_by_side(*c, **kw):
        charts.extend(c)
        options.append(kw)
        return b"png"

    monkeypatch.setattr(daily_zone_1h.alert_chart, "render_side_by_side", side_by_side)
    monkeypatch.setattr(telegram, "post_photo", lambda png, text, chat_id=None, bot_token=None: 7)
    monkeypatch.setattr(trade_tracker, "track", lambda *args: tracked.append(args))
    monkeypatch.setattr(trade_tracker.mt5_trader, "open_trade", lambda *args: opened.append(args))
    s = setup()
    assert daily_zone_1h.send_alert(s)
    assert [args[1] for args, _ in charts] == ["EUR/USD · 1d", "EUR/USD · 4h", "EUR/USD · 1h"]
    assert all(kwargs.get("plan") is None for _, kwargs in charts)
    # the daily zone's turns and the return circled; both breaks boxed, no arrow, no numbers
    assert charts[0][1]["touches"] == [*s.turns, daily_zone_1h._iso(s.candles_1d[-1].time)]
    assert charts[2][1]["boxes"] == [(s.broke, s.broke), (s.time, s.time)]
    assert "breakout" not in charts[2][1] and "numbers" not in charts[2][1]
    assert options == [dict(size=daily_zone_1h.PICTURE_SIZE, divider=True, widths=daily_zone_1h.WIDTHS)]
    assert tracked == [] and opened == []


def test_the_picture_draws():
    assert daily_zone_1h.render(setup()).startswith(b"\x89PNG")


def test_an_example_says_so():
    s = setup()
    assert "דוגמה" in format_message(s, example=True)
    assert "דוגמה" not in format_message(s)
