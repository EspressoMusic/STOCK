from datetime import date, datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import penny_scan
from app.db import Base
from app.models import SentAlert
from app.penny_scan import PennyScanResult, format_message, run_penny_live, spikes_from_quotes

CLOSE = 1793131200  # 27/10/26 16:00 New York
NY = ZoneInfo("America/New_York")


def quote(symbol="ABCD", price=1.0, change=30.0, volume=2_000_000, average=500_000, at=CLOSE, name="Abcd Inc."):
    return {
        "symbol": symbol, "shortName": name, "regularMarketPrice": price,
        "regularMarketChangePercent": change, "regularMarketVolume": volume,
        "averageDailyVolume3Month": average, "regularMarketTime": at,
    }


def symbols(quotes):
    return [s.symbol for s in spikes_from_quotes(quotes)[0]]


def test_biggest_gain_first_with_its_session():
    spikes, session = spikes_from_quotes([quote("LOW", change=25), quote("HIGH", change=60)])
    assert [s.symbol for s in spikes] == ["HIGH", "LOW"]
    assert spikes[0].volume_ratio == 4.0
    assert str(session) == "2026-10-27"


def test_price_gain_and_volume_rules():
    assert symbols([quote(price=5.2)]) == []  # not a penny stock
    assert symbols([quote(price=0.05, volume=50_000_000, average=1_000_000)]) == []  # sub-penny junk
    assert symbols([quote(change=15)]) == []
    assert symbols([quote(volume=1_000_000, average=500_000)]) == []  # only ×2 the usual volume
    assert symbols([quote(average=0)]) == []  # no history to compare with
    assert symbols([quote(price=0.2, volume=2_000_000)]) == []  # $400k traded — too thin


def test_stale_quotes_warrants_and_nameless_listings_are_out():
    fresh = quote("NEW")
    assert symbols([fresh, quote("OLD", at=CLOSE - 86400)]) == ["NEW"]
    assert symbols([fresh, quote("ABCDW", name="Abcd Warrant")]) == ["NEW"]
    assert symbols([fresh, quote("OPENZ", name=None)]) == ["NEW"]
    assert symbols([fresh, quote("ABC-WT")]) == ["NEW"]


def test_four_letter_tickers_ending_like_a_warrant_are_stocks():
    assert symbols([quote("NEXR", name="Nexera Technologies Ltd")]) == ["NEXR"]


def test_no_quotes_no_session():
    assert spikes_from_quotes([]) == ([], None)


def test_caption():
    s = spikes_from_quotes([quote(price=3.31, change=56.1, volume=45_279_156, average=863_347)])[0][0]
    text = format_message(s)
    assert text.startswith("🚀 <b>ABCD</b> · $3.31\nעלתה 56% · מחזור פי 52 מהרגיל\n")
    assert "tradingview.com/chart/?symbol=ABCD" in text


def test_live_needs_only_a_usual_days_volume_so_far():
    q = quote(volume=600_000, average=500_000)
    assert symbols([q]) == []
    s = spikes_from_quotes([q], penny_scan.LIVE_MIN_VOLUME_RATIO)[0][0]
    assert format_message(s, live=True).startswith("🚀 <b>ABCD</b> · $1\nעולה עכשיו 30% · מחזור עד כה פי 1.2 מיום רגיל\n")


def at(hour, minute, day=27):
    class Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, day, hour, minute, tzinfo=NY)
    return Now


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[SentAlert.__table__])
    monkeypatch.setattr(penny_scan.telegram, "is_configured", lambda: True)
    monkeypatch.setattr(penny_scan.settings, "telegram_penny_bot_token", "token")
    monkeypatch.setattr(penny_scan, "SEND_INTERVAL_SECONDS", 0)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def live_scan(monkeypatch, session=date(2026, 10, 27)):
    spikes = spikes_from_quotes([quote("AAA"), quote("BBB")])[0]
    scan = Mock(return_value=PennyScanResult("", session, spikes))
    monkeypatch.setattr(penny_scan, "scan_penny_spikes", scan)
    send = Mock(return_value=True)
    monkeypatch.setattr(penny_scan, "send_alert", send)
    return scan, send


def test_live_sends_each_stock_once_a_day(monkeypatch, db):
    monkeypatch.setattr(penny_scan, "datetime", at(10, 2))
    scan, send = live_scan(monkeypatch)
    assert run_penny_live(db) == 2
    assert run_penny_live(db) == 0
    assert send.call_count == 2 and send.call_args.kwargs["live"]
    assert scan.call_args.args == (penny_scan.LIVE_MIN_VOLUME_RATIO,)


def test_live_only_in_the_session_and_not_on_a_holiday(monkeypatch, db):
    scan, send = live_scan(monkeypatch)
    for now in (at(9, 27), at(16, 2), at(11, 0, day=31)):  # before, after, a Saturday
        monkeypatch.setattr(penny_scan, "datetime", now)
        assert run_penny_live(db) == 0
    assert not scan.called
    monkeypatch.setattr(penny_scan, "datetime", at(10, 2))
    live_scan(monkeypatch, session=date(2026, 10, 26))  # the screener still holds yesterday's
    assert run_penny_live(db) == 0
