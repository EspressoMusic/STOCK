from datetime import datetime
from zoneinfo import ZoneInfo

from app import tight_touch_scan
from app.candles import Candle
from app.tight_touch_scan import closed, signal
from app.zones import Zone

NY = ZoneInfo("America/New_York")


def bars(*rows):
    """(open, high, low, close) rows, 4 hours apart."""
    return [Candle(time=i * 14400, open=o, high=h, low=l, close=c) for i, (o, h, l, c) in enumerate(rows)]


# A zone at 100-101 (reach 0.25 each side), touched from below on candles 1 and 4.
ZONE = Zone(id="z", bottom=100, top=101, unit=1, start_idx=1, touch_idx=[1, 4])
BELOW = (97, 98, 96, 97)
UP_TO_IT = (97, 100.5, 96.5, 99.5)
TWO_TOUCHES = [BELOW, UP_TO_IT, BELOW, BELOW, UP_TO_IT, BELOW, BELOW]


def zones(monkeypatch, *found):
    monkeypatch.setattr(tight_touch_scan, "detect_zones", lambda *a, **k: list(found))


def test_third_visit_from_below_closing_under_the_last_body_is_a_short(monkeypatch):
    zones(monkeypatch, ZONE)
    window = bars(*TWO_TOUCHES, (97, 100.6, 97, 99.8), (99.8, 100, 96, 96.5))
    direction, zone, visit = signal(window)
    assert (direction, zone.touch_idx, visit) == ("down", [1, 4], 7)


def test_green_candle_closing_under_the_last_body_is_no_short(monkeypatch):
    zones(monkeypatch, ZONE)
    window = bars(*TWO_TOUCHES, (97, 100.6, 97, 99.8), (96, 100, 95.5, 96.5))
    assert signal(window) is None


def test_close_inside_the_last_body_is_no_signal(monkeypatch):
    zones(monkeypatch, ZONE)
    window = bars(*TWO_TOUCHES, (97, 100.6, 97, 99.8), (99.8, 100, 97.5, 98))
    assert signal(window) is None


def test_touches_without_leaving_the_zone_in_between_are_one_visit(monkeypatch):
    zones(monkeypatch, ZONE)
    stuck = [BELOW, UP_TO_IT, (99.5, 100.4, 99, 99.6), (99.6, 100.3, 99, 99.5), UP_TO_IT, BELOW, BELOW]
    window = bars(*stuck, (97, 100.6, 97, 99.8), (99.8, 100, 96, 96.5))
    assert signal(window) is None


def test_touches_longer_ago_than_the_span_dont_count(monkeypatch):
    zones(monkeypatch, ZONE)
    window = bars(*TWO_TOUCHES, *[BELOW] * tight_touch_scan.SPAN_BARS, (97, 100.6, 97, 99.8), (99.8, 100, 96, 96.5))
    assert signal(window) is None


def test_visit_that_began_earlier_is_no_new_signal(monkeypatch):
    zones(monkeypatch, ZONE)
    window = bars(*TWO_TOUCHES, UP_TO_IT, (99.5, 100.6, 99, 99.8), (99.8, 100, 96, 96.5))
    assert signal(window) is None


def ny(day, hour, minute):
    return int(datetime(2026, 10, day, hour, minute, tzinfo=NY).timestamp())


def test_candle_in_progress_is_dropped():
    first, second = Candle(ny(28, 9, 30), 1, 1, 1, 1), Candle(ny(28, 13, 30), 1, 1, 1, 1)
    assert closed([first, second], datetime(2026, 10, 28, 13, 35, tzinfo=NY)) == [first]
    assert closed([first, second], datetime(2026, 10, 28, 16, 5, tzinfo=NY)) == [first, second]
    assert closed([first], datetime(2026, 10, 28, 13, 25, tzinfo=NY)) == []


def test_caption_marks_type_2():
    s = tight_touch_scan.TightTouchSetup("AAPL", "down", "2026-10-28T17:30:00+00:00", 100, 101, [])
    assert "סוג 2" in tight_touch_scan.format_message(s)
    assert "🔴 שורט" in tight_touch_scan.format_message(s)
