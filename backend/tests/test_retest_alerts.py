from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import retest_alerts
from app.candles import Candle
from app.db import Base
from app.models import SentAlert
from app.retest_alerts import RetestSetup, find_retests, find_setup, run_retest_alerts
from app.zones import Breakout, Zone

# Zone 100-102, one typical candle = 2: after a breakdown, a touch reaches 99.5 and
# closes under 101, and a close over 102.2 fails the breakdown.
ZONE = Zone(id="z", bottom=100, top=102, unit=2, start_idx=0, touch_idx=[])
DOWN = Breakout(idx=0, dir="down")


def bars(*rows):
    return [Candle(time=i * 3600, open=close, high=high, low=low, close=close) for i, (low, high, close) in enumerate(rows)]


# Price hangs right under the broken zone and pokes it three times (the user's GBP/CHF
# example on the 1h chart).
THREE_TOUCHES = [
    (98, 103, 98.5),  # 0 breakdown
    (98, 99.6, 99),  # reaches the zone, but the next candle goes higher
    (98.5, 99.8, 99.2),  # 2 touch
    (98.2, 99.4, 98.6),
    (97.8, 99, 98.4),
    (98, 100.3, 99),  # 5 touch, into the zone and back out
    (98.3, 99.5, 98.8),  # still at the zone, but lower than the touch before it
    (97.9, 99.1, 98.5),
    (98.2, 99.9, 99.1),  # 8 touch
    (98, 99.3, 98.7),
]


def test_each_poke_at_the_zone_is_one_touch():
    assert find_retests(bars(*THREE_TOUCHES), ZONE, DOWN) == [2, 5, 8]


def test_close_back_through_the_zone_fails_the_breakout():
    assert find_retests(bars(*THREE_TOUCHES, (99, 103, 102.5)), ZONE, DOWN) == []


def test_poke_that_closes_above_the_middle_is_not_a_touch():
    rows = THREE_TOUCHES[:5] + [(98, 101.5, 101.2)] + THREE_TOUCHES[6:]
    assert find_retests(bars(*rows), ZONE, DOWN) == [2, 8]


def test_last_candle_counts_before_later_candles_confirm_it():
    assert find_retests(bars(*THREE_TOUCHES[:9]), ZONE, DOWN) == [2, 5, 8]
    # ...and a higher poke right after it takes its place.
    assert find_retests(bars(*THREE_TOUCHES, (98.1, 100, 99.2)), ZONE, DOWN) == [2, 5, 10]


def test_touch_long_after_the_previous_one_does_not_count():
    rows = THREE_TOUCHES[:8] + [(97, 98, 97.5)] * 30 + [(98.2, 99.9, 99.1)]
    assert find_retests(bars(*rows), ZONE, DOWN) == [2, 5]


def test_breakout_up_mirrors_breakdown():
    flipped = [(-high + 202, -low + 202, -close + 202) for low, high, close in THREE_TOUCHES]
    assert find_retests(bars(*flipped), ZONE, Breakout(idx=0, dir="up")) == [2, 5, 8]


@pytest.fixture
def one_zone(monkeypatch):
    monkeypatch.setattr(retest_alerts, "detect_zones", lambda candles, **_: [ZONE])
    monkeypatch.setattr(retest_alerts, "find_breakouts", lambda candles, zone: [DOWN])


def test_alerts_on_the_third_touch_while_it_is_fresh(one_zone):
    quiet = (98, 99, 98.5)
    assert find_setup(bars(*THREE_TOUCHES))[2] == [2, 5, 8]
    assert find_setup(bars(*THREE_TOUCHES, *[quiet] * 7)) is not None
    assert find_setup(bars(*THREE_TOUCHES, *[quiet] * 8)) is None


def test_two_touches_are_not_enough(one_zone):
    assert find_setup(bars(*THREE_TOUCHES[:8])) is None


def setup(symbol):
    return RetestSetup(
        symbol=symbol, name=None, direction="up", zone_bottom=1, zone_top=2,
        zone_start_time="2026-08-01T04:00:00+00:00", price=2.5,
        breakout_time="2026-10-01T04:00:00+00:00", retest_times=["2026-10-20T04:00:00+00:00"] * 3,
        key=f"k:{symbol}",
    )


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[SentAlert.__table__])
    monkeypatch.setattr(retest_alerts.telegram, "is_configured", lambda: True)
    monkeypatch.setattr(retest_alerts, "SEND_INTERVAL_SECONDS", 0)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_each_setup_is_sent_once(monkeypatch, db):
    monkeypatch.setattr(retest_alerts, "scan_retest_setups", lambda: (100, [setup("AAA"), setup("BBB")]))
    send = Mock(return_value=True)
    monkeypatch.setattr(retest_alerts.telegram, "send_message", send)

    assert run_retest_alerts(db).sent_count == 2
    assert run_retest_alerts(db).sent_count == 0
    assert send.call_count == 2


def test_failed_send_is_retried_next_run(monkeypatch, db):
    monkeypatch.setattr(retest_alerts, "scan_retest_setups", lambda: (100, [setup("AAA")]))
    monkeypatch.setattr(retest_alerts.telegram, "send_message", Mock(return_value=False))
    assert run_retest_alerts(db).sent_count == 0

    monkeypatch.setattr(retest_alerts.telegram, "send_message", Mock(return_value=True))
    assert run_retest_alerts(db).sent_count == 1


def test_message_links_to_the_scanner_with_the_marking():
    text = retest_alerts.format_message(setup("AAA"))
    assert "https://www.tradingview.com/chart/?symbol=AAA" in text
    hour = 1792468800  # 2026-10-20T04:00:00Z
    assert (
        f"?symbol=AAA&amp;tf=1h&amp;zone=1,2&amp;from=1785556800&amp;dir=up"
        f"&amp;breakout=1790827200&amp;retests={hour},{hour},{hour}"
    ) in text


def test_forex_pair_shows_its_name_and_opens_on_tradingview():
    s = setup("GBPCHF=X")
    s.name = "GBP/CHF"
    text = retest_alerts.format_message(s)
    assert "<b>GBP/CHF</b>" in text
    assert 'href="https://www.tradingview.com/chart/?symbol=GBPCHF"' in text
