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

# Zone 100-102, one typical candle = 2: price has "left" above 104, a retest reaches
# 102.5 and closes above 101, and a close under 99.8 fails the breakout.
ZONE = Zone(id="z", bottom=100, top=102, unit=2, start_idx=0, touch_idx=[])
UP = Breakout(idx=0, dir="up")


def bars(*rows):
    return [Candle(time=i * 86400, open=close, high=high, low=low, close=close) for i, (low, high, close) in enumerate(rows)]


THREE_RETESTS = [
    (99, 103.5, 103),  # 0 breakout
    (103, 106, 105),  # left
    (102, 105, 103.5),  # 2 retest
    (102.2, 104, 103),  # still sitting on the zone: same retest, not a new one
    (103, 107, 106),  # left
    (101.5, 106, 104),  # 5 retest
    (104, 108, 107),  # left
    (102.4, 107, 105),  # 7 retest
]


def test_counts_each_pullback_once():
    assert find_retests(bars(*THREE_RETESTS), ZONE, UP) == [2, 5, 7]


def test_close_back_through_the_zone_fails_the_breakout():
    assert find_retests(bars(*THREE_RETESTS, (98, 104, 99)), ZONE, UP) == []


def test_dip_that_closes_below_the_middle_is_not_a_retest():
    rows = THREE_RETESTS[:5] + [(100.2, 104, 100.8)]
    assert find_retests(bars(*rows), ZONE, UP) == [2]


def test_retest_long_after_the_previous_one_does_not_count():
    rows = THREE_RETESTS[:5] + [(104, 108, 107)] * 30 + [(101.5, 106, 104)]
    assert find_retests(bars(*rows), ZONE, UP) == [2]


def test_breakdown_mirrors_breakout():
    flipped = [(-high + 202, -low + 202, -close + 202) for low, high, close in THREE_RETESTS]
    assert find_retests(bars(*flipped), ZONE, Breakout(idx=0, dir="down")) == [2, 5, 7]


QUICK_RETEST = [
    (99, 103, 102.5),  # 0 breakout, barely clear of the zone
    (101.8, 103, 102.6),  # still on the zone
    (101.5, 103, 102.4),  # 2 pulls back below the last low into the zone, holds above 101
]


def test_quick_retest_before_price_got_clear():
    rows = QUICK_RETEST + [(102.5, 105, 104.5), (102, 105, 103.5)]  # then clear above 104, retest again
    assert find_retests(bars(*rows), ZONE, UP) == [2, 4]


def test_quick_retest_needs_price_to_get_clear_after():
    rows = QUICK_RETEST + [(102.5, 104, 103.5)] * 5  # past the retest's high, never above 104
    assert find_retests(bars(*rows), ZONE, UP) == []


def test_candle_right_after_the_breakout_is_not_a_quick_retest():
    rows = [(99, 103, 102.5), (101.5, 103, 102.4), (102.5, 105, 104.5)]
    assert find_retests(bars(*rows), ZONE, UP) == []


def test_quick_breakdown_retest_mirrors_breakout():
    rows = QUICK_RETEST + [(102.5, 105, 104.5), (102, 105, 103.5)]
    flipped = [(-high + 202, -low + 202, -close + 202) for low, high, close in rows]
    assert find_retests(bars(*flipped), ZONE, Breakout(idx=0, dir="down")) == [2, 4]


@pytest.fixture
def one_zone(monkeypatch):
    monkeypatch.setattr(retest_alerts, "detect_zones", lambda candles, **_: [ZONE])
    monkeypatch.setattr(retest_alerts, "find_breakouts", lambda candles, zone: [UP])


def test_alerts_on_the_third_retest_while_it_is_fresh(one_zone):
    assert find_setup(bars(*THREE_RETESTS))[2] == [2, 5, 7]
    assert find_setup(bars(*THREE_RETESTS, *[(104, 108, 107)] * 3)) is not None
    assert find_setup(bars(*THREE_RETESTS, *[(104, 108, 107)] * 4)) is None


def test_two_retests_are_not_enough(one_zone):
    assert find_setup(bars(*THREE_RETESTS[:6])) is None


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
    day = 1792468800  # 2026-10-20T04:00:00Z
    assert (
        f"?symbol=AAA&amp;tf=1d&amp;zone=1,2&amp;from=1785556800&amp;dir=up"
        f"&amp;breakout=1790827200&amp;retests={day},{day},{day}"
    ) in text
