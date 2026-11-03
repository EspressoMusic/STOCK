from app import touch3_15m
from app.candles import Candle
from app.touch3_15m import find_setup, setup_from, side_zones
from app.zones import Zone, detect_zones


def bars(*rows):
    return [Candle(time=i * 900, open=o, high=h, low=lo, close=c) for i, (o, h, lo, c) in enumerate(rows)]


# Zone 100-101, one typical candle = 1; side_zones is stubbed, so only the touches'
# indexes and the last candles matter.
def zone(*touches):
    return Zone(id="z", bottom=100, top=101, unit=1, start_idx=touches[0], touch_idx=list(touches))


ABOVE = (103, 104, 102, 103)
DOWN_ONTO_IT = (102, 102.5, 100.8, 101.2)
ENTRY_UP = (101.2, 102.4, 101, 102.3)
# Came down onto the zone (candle 4) and a green candle closes over its body.
LONG = bars(*[ABOVE] * 4, DOWN_ONTO_IT, ENTRY_UP)


def stub(monkeypatch, *zones, side="up"):
    """The zones as touched from this side only — lows ("up") or highs ("down"); no other
    zones around for the ranging check."""
    monkeypatch.setattr(touch3_15m, "side_zones", lambda *a, **k: [(side, z) for z in zones])
    monkeypatch.setattr(touch3_15m, "detect_zones", lambda *a, **k: [])


def test_the_third_touch_from_above_is_a_long(monkeypatch):
    stub(monkeypatch, zone(0, 2))
    assert find_setup(LONG) == ("up", zone(0, 2), 4)


def test_the_fourth_touch_is_not_the_third(monkeypatch):
    stub(monkeypatch, zone(0, 1, 2))
    assert find_setup(LONG) is None


def test_a_touch_too_long_before_does_not_count(monkeypatch):
    old = touch3_15m.MAX_GAP_BARS + 5
    window = bars(*[ABOVE] * (old + 4), DOWN_ONTO_IT, ENTRY_UP)
    stub(monkeypatch, zone(0, old + 2))
    assert find_setup(window) is None
    # Three touches, the oldest out of reach: the visit is still only the third.
    stub(monkeypatch, zone(0, old, old + 2))
    assert find_setup(window) == ("up", zone(old, old + 2), old + 4)


def test_reaching_the_zone_is_no_alert_yet(monkeypatch):
    stub(monkeypatch, zone(0, 2))
    assert find_setup(LONG[:-1]) is None


def test_the_short_mirrors_the_long(monkeypatch):
    stub(monkeypatch, zone(0, 2), side="down")
    # Mirrored around 201, the zone stays 100-101: up into it from below, a red close under.
    rows = [ABOVE] * 4 + [DOWN_ONTO_IT, ENTRY_UP]
    window = bars(*[(201 - o, 201 - lo, 201 - h, 201 - c) for o, h, lo, c in rows])
    assert find_setup(window) == ("down", zone(0, 2), 4)


def test_touches_from_the_other_side_do_not_count(monkeypatch):
    # Two swing highs at the zone, and now price comes down onto it from above.
    stub(monkeypatch, zone(0, 2), side="down")
    assert find_setup(LONG) is None


def test_a_close_through_the_zone_in_between_spoils_it(monkeypatch):
    stub(monkeypatch, zone(0, 2))
    through = bars(ABOVE, (100, 100.5, 98, 98.5), *[ABOVE] * 2, DOWN_ONTO_IT, ENTRY_UP)
    assert find_setup(through) is None


def test_a_little_past_the_zone_still_holds(monkeypatch):
    stub(monkeypatch, zone(0, 2))
    poked = bars(ABOVE, (100, 100.5, 99.5, 99.7), *[ABOVE] * 2, DOWN_ONTO_IT, ENTRY_UP)
    assert find_setup(poked) == ("up", zone(0, 2), 4)


def test_the_third_touch_is_a_new_visit(monkeypatch):
    # Candle 3 never got clear of the zone after the touch on candle 2.
    stub(monkeypatch, zone(0, 2))
    stayed = bars(ABOVE, ABOVE, ABOVE, (102, 102.5, 101.1, 102), DOWN_ONTO_IT, ENTRY_UP)
    assert find_setup(stayed) is None


def test_wicks_of_one_visit_are_one_touch():
    # Lows at 100.5 (candle 3) and 100.45 (5) with no getting clear between, then 100.5 again (11).
    rows = [
        (104, 104.5, 103.5, 104), (103, 103.5, 102.5, 103), (102, 102.5, 101.5, 102), (101, 101.5, 100.5, 101),
        (101, 101.2, 100.7, 101), (101, 101.3, 100.45, 101), (101, 102, 101, 102), (103, 103.5, 102.5, 103),
        (104, 104.5, 103.5, 104), (103, 103.5, 102.5, 103), (102, 102.5, 101.5, 102), (101, 101.5, 100.5, 101),
        (102, 102.5, 101.5, 102), (103, 103.5, 102.5, 103),
    ]
    assert [(d, z.touch_idx) for d, z in side_zones(bars(*rows))] == [("up", [3, 11])]


def test_side_zones_keep_highs_and_lows_apart():
    # Two swing highs at ~101.5 (candles 3 and 9), later a swing low there (candle 20).
    mids = [98, 99, 100, 101, 100, 99, 98, 99, 100, 101, 100, 99, 98, 99, 100, 101, 102, 103, 104, 103, 102, 103, 104, 105, 106]
    window = [Candle(time=i * 900, open=m, high=m + 0.5, low=m - 0.5, close=m) for i, m in enumerate(mids)]
    assert [z.touch_idx for z in detect_zones(window, min_touches=2)][0] == [3, 9, 20]
    assert [(d, z.touch_idx) for d, z in side_zones(window)] == [("down", [3, 9]), ("up", [6, 12])]


def test_only_when_the_daily_candle_agrees(monkeypatch):
    stub(monkeypatch, zone(0, 2))
    monkeypatch.setattr(touch3_15m, "confirms", lambda daily, direction: direction == "down")
    assert setup_from("EURUSD=X", "EUR/USD", LONG, []) is None
    monkeypatch.setattr(touch3_15m, "confirms", lambda daily, direction: True)
    s = setup_from("EURUSD=X", "EUR/USD", LONG, [])
    assert s.direction == "up" and s.plan.entry == 102.3 and s.plan.stop < 100
    assert s.plan.target - s.plan.entry == 3 * (s.plan.entry - s.plan.stop)
    assert len(s.touch_times) == 3  # the two touches, then the visit's first candle
