from app import nasdaq_scalp
from app.candles import Candle
from app.nasdaq_scalp import EMA_PERIOD, MAX_GAP_BARS, ema_signal, recent_touches, zone_signal
from app.zones import Zone


def bars(*rows, start=0):
    """(open, high, low, close) rows, 5 minutes apart."""
    return [Candle(time=start + i * 300, open=o, high=h, low=l, close=c) for i, (o, h, l, c) in enumerate(rows)]


# The EMA flat at 100; the last three candles are the one before the touch, the touch
# and the signal candle.
FLAT = [(105, 106, 104, 105)] * EMA_PERIOD
EMA = [100.0] * (EMA_PERIOD + 3)


def test_long_after_a_touch_from_above_and_a_close_over_its_body():
    candles = bars(*FLAT, (103, 104, 102, 102.5), (102.5, 103, 99.5, 101), (101, 104, 100.8, 103))
    assert ema_signal(candles, EMA, len(candles) - 1) == "up"


def test_close_inside_the_touch_candles_body_is_no_signal():
    candles = bars(*FLAT, (103, 104, 102, 102.5), (102.5, 103, 99.5, 101), (101, 102.8, 100.8, 102))
    assert ema_signal(candles, EMA, len(candles) - 1) is None


def test_touch_must_come_from_a_candle_clear_of_the_ema():
    # The candle before the touch already reached the EMA, so it was the touch.
    candles = bars(*FLAT, (103, 104, 99.8, 102.5), (102.5, 103, 99.5, 101), (101, 104, 100.8, 103))
    assert ema_signal(candles, EMA, len(candles) - 1) is None


def test_short_after_a_touch_from_below_and_a_close_under_its_body():
    candles = bars(*FLAT, (97, 98, 96, 97.5), (97.5, 100.5, 97, 99), (99, 99.2, 96, 96.5))
    assert ema_signal(candles, EMA, len(candles) - 1) == "down"


def test_red_candle_closing_over_the_touch_body_is_no_long():
    candles = bars(*FLAT, (103, 104, 102, 102.5), (102.5, 103, 99.5, 101), (104, 104.5, 102.8, 103))
    assert ema_signal(candles, EMA, len(candles) - 1) is None


def test_model_1_stop_is_behind_the_candle_on_the_line():
    candles = bars(*FLAT, (103, 104, 102, 102.5), (102.5, 103, 99.5, 101), (101, 104, 100.8, 103))
    s = next(s for s in nasdaq_scalp.signals_at(candles, EMA, [50.0] * len(candles), len(candles) - 1) if s.model == 1)
    assert s.plan.entry == 103 and 99 < s.plan.stop < 99.5
    assert s.plan.target == 103 + 3 * (103 - s.plan.stop)


def test_touches_before_a_long_gap_are_dropped():
    zone = Zone(id="z", bottom=100, top=101, unit=1, start_idx=0, touch_idx=[0, 100, 110])
    assert recent_touches(zone, 110 + MAX_GAP_BARS) == [100, 110]
    assert recent_touches(zone, 111 + MAX_GAP_BARS) == []


# A zone at 100-101 (reach 0.25 each side) touched on candles 0-2; only the last few
# candles matter to zone_signal.
ZONE = Zone(id="z", bottom=100, top=101, unit=1, start_idx=0, touch_idx=[0, 1, 2])
ABOVE = (103, 104, 102, 103)
DOWN_ONTO_IT = (102, 102.5, 100.8, 101.2)


def test_green_candle_over_the_body_after_coming_down_onto_a_zone_is_a_long(monkeypatch):
    monkeypatch.setattr(nasdaq_scalp, "detect_zones", lambda *a, **k: [ZONE])
    window = bars(*[ABOVE] * 4, DOWN_ONTO_IT, (101.2, 102.4, 101, 102.3))
    assert zone_signal(window) == ("up", ZONE, 4)


def test_model_2_only_with_the_stoch_rsi_oversold_for_a_long(monkeypatch):
    monkeypatch.setattr(nasdaq_scalp, "detect_zones", lambda *a, **k: [ZONE])
    candles = bars(*[ABOVE] * 4, DOWN_ONTO_IT, (101.2, 102.4, 101, 102.3))
    model_2 = lambda k: [s for s in nasdaq_scalp.signals_at(candles, [0.0] * 6, [50.0] * 5 + [k], 5) if s.model == 2]
    assert [s.direction for s in model_2(15.0)] == ["up"]
    assert model_2(50.0) == [] and model_2(85.0) == [] and model_2(float("nan")) == []


def test_reaching_the_zone_is_no_signal_yet(monkeypatch):
    monkeypatch.setattr(nasdaq_scalp, "detect_zones", lambda *a, **k: [ZONE])
    assert zone_signal(bars(*[ABOVE] * 4, DOWN_ONTO_IT)) is None


def test_entry_candle_closing_above_the_zone_is_no_short(monkeypatch):
    monkeypatch.setattr(nasdaq_scalp, "detect_zones", lambda *a, **k: [ZONE])
    below = (98, 99, 97, 98)
    window = bars(*[below] * 4, (98, 101.1, 97.8, 100.8), (101.6, 102.5, 101, 102.2), (102.2, 102.3, 101.1, 101.4))
    assert zone_signal(window) is None


def test_m1_zones_never_reach_into_the_day_before(monkeypatch):
    seen = []
    monkeypatch.setattr(nasdaq_scalp, "zone_signal", lambda window, *a: seen.append(window))
    # Yesterday's last two minutes, then today's first three.
    candles = [Candle(time=t, open=1, high=1, low=1, close=1) for t in (0, 60, 64800, 64860, 64920)]
    nasdaq_scalp.m1_signal_at(candles, 4)
    assert seen == [candles[2:]]


def test_second_entry_candle_of_a_visit_is_no_new_signal(monkeypatch):
    monkeypatch.setattr(nasdaq_scalp, "detect_zones", lambda *a, **k: [ZONE])
    window = bars(*[ABOVE] * 4, DOWN_ONTO_IT, (101.2, 102.4, 101, 102.3), (102.3, 102.9, 101.1, 102.8))
    assert zone_signal(window[:-1]) is not None
    assert zone_signal(window) is None


def m1(times, price):
    return [Candle(time=t, open=price, high=price + 1, low=price - 1, close=price) for t in times]


# 28/10/26 9:30 New York.
OPEN = 1793194200


def test_premarket_comes_from_the_etf_times_the_days_ratio():
    session = m1([OPEN, OPEN + 60], 400.0 * 41)
    etf = m1([OPEN - 7200 - 60, OPEN - 120, OPEN - 60], 399.0) + m1([OPEN, OPEN + 60], 400.0)
    out = nasdaq_scalp.with_premarket(session, etf)
    # Only the last two hours before the open, scaled by 41.
    assert [c.time for c in out] == [OPEN - 120, OPEN - 60, OPEN, OPEN + 60]
    assert out[0].close == 399.0 * 41 and out[0].high == 400.0 * 41
    assert out[2:] == session


def test_a_day_the_etf_has_no_session_minutes_for_gets_no_premarket():
    session = m1([OPEN, OPEN + 60], 16400.0)
    assert nasdaq_scalp.with_premarket(session, m1([OPEN - 60], 399.0)) == session


def test_no_entry_in_the_sessions_last_hour(monkeypatch):
    monkeypatch.setattr(nasdaq_scalp, "detect_zones", lambda *a, **k: [ZONE])
    rows = (*[ABOVE] * 4, DOWN_ONTO_IT, (101.2, 102.4, 101, 102.3))

    def model_2(close):  # the signal candle closing then
        candles = bars(*rows, start=close - 6 * 300)
        return [s for s in nasdaq_scalp.signals_at(candles, [0.0] * 6, [15.0] * 6, 5) if s.model == 2]

    three_pm = OPEN + 5 * 3600 + 1800
    assert model_2(three_pm - 300)
    assert model_2(three_pm) == [] and model_2(three_pm + 3600) == []


def test_no_m1_entry_in_the_sessions_last_hour(monkeypatch):
    seen = []
    monkeypatch.setattr(nasdaq_scalp, "zone_signal", lambda window, *a: seen.append(window))
    three_pm = OPEN + 5 * 3600 + 1800
    candles = m1([three_pm - 120, three_pm - 60], 100.0)
    nasdaq_scalp.m1_signal_at(candles, 0)
    assert nasdaq_scalp.m1_signal_at(candles, 1) is None
    assert seen == [candles[:1]]


def test_no_m1_signal_on_a_premarket_candle(monkeypatch):
    seen = []
    monkeypatch.setattr(nasdaq_scalp, "zone_signal", lambda window, *a: seen.append(window))
    candles = m1([OPEN - 60, OPEN], 100.0)
    assert nasdaq_scalp.m1_signal_at(candles, 0) is None
    nasdaq_scalp.m1_signal_at(candles, 1)
    assert seen == [candles]
