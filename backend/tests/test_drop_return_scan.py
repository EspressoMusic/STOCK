from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import drop_return_scan
from app.candles import Candle
from app.db import Base
from app.drop_return_scan import DropReturnSetup, find_setup, run_drop_return_alerts, setup_from
from app.models import SentAlert
from app.zones import Breakout, Zone

# Zone 100-102, one typical candle = 2: a candle reaching 99.5 is back at it, and the
# fall has to reach 92.
ZONE = Zone(id="z", bottom=100, top=102, unit=2, start_idx=0, touch_idx=[])


def bars(*rows):
    return [Candle(time=i * 900, open=o, high=h, low=lo, close=c) for i, (o, h, lo, c) in enumerate(rows)]


# Breaks down out of the zone, falls to 91, rallies back up to it, and a red candle
# closes under the one before — the short (the user's example, 29/10/26).
BREAKDOWN = (101, 101.5, 98, 98.5)
FALL = [
    (98.5, 98.8, 95, 95.5),
    (95.5, 96, 91, 91.5),
    (91.5, 95, 91.2, 94.8),
    (94.8, 98, 94.5, 97.8),
]
RETURN = [
    (97.8, 100.2, 97.5, 99.8),  # back at the zone
    (99.8, 100, 97, 97.2),  # the entry candle
]


@pytest.fixture
def one_zone(monkeypatch):
    monkeypatch.setattr(drop_return_scan, "detect_zones", lambda candles, **_: [ZONE])
    monkeypatch.setattr(drop_return_scan, "find_breakouts", lambda candles, zone: [Breakout(idx=0, dir="down")])


def test_fall_and_return_to_the_zone_is_a_short(one_zone):
    assert find_setup(bars(BREAKDOWN, *FALL, *RETURN)) == ("down", ZONE, 0, 5)


def flip(rows):
    # Mirrored around 202, the zone stays 100-102: a breakout up, a rise and a return down.
    return [(202 - o, 202 - lo, 202 - h, 202 - c) for o, h, lo, c in rows]


def test_rise_and_return_down_to_the_zone_is_a_long(one_zone, monkeypatch):
    monkeypatch.setattr(drop_return_scan, "find_breakouts", lambda candles, zone: [Breakout(idx=0, dir="up")])
    assert find_setup(bars(*flip([BREAKDOWN, *FALL, *RETURN]))) == ("up", ZONE, 0, 5)


def test_no_alert_before_the_entry_candle(one_zone):
    assert find_setup(bars(BREAKDOWN, *FALL, RETURN[0])) is None


def test_a_small_dip_is_not_a_fall(one_zone):
    shallow = [(o, h, max(lo, 93), max(c, 93.5)) for o, h, lo, c in FALL]
    assert find_setup(bars(BREAKDOWN, *shallow, *RETURN)) is None


def test_only_the_first_return_counts(one_zone):
    fall = FALL[:2] + [(91.5, 99.6, 91.2, 94.8)] + FALL[3:]  # already reached the zone once
    assert find_setup(bars(BREAKDOWN, *fall, *RETURN)) is None


def test_a_return_days_later_does_not_count(one_zone):
    quiet = [(95, 96, 94, 95)] * drop_return_scan.MAX_RETURN_BARS
    assert find_setup(bars(BREAKDOWN, *FALL, *quiet, *RETURN)) is None


def test_only_after_a_breakout_the_same_way(one_zone, monkeypatch):
    monkeypatch.setattr(drop_return_scan, "find_breakouts", lambda candles, zone: [Breakout(idx=0, dir="up")])
    assert find_setup(bars(BREAKDOWN, *FALL, *RETURN)) is None
    monkeypatch.setattr(drop_return_scan, "find_breakouts", lambda candles, zone: [Breakout(idx=0, dir="down")])
    assert find_setup(bars(*flip([BREAKDOWN, *FALL, *RETURN]))) is None


def test_trade_needs_the_daily_candle_and_stops_behind_the_zone(one_zone):
    red, green = Candle(time=0, open=1.1, high=1.2, low=0.9, close=1.0), Candle(time=0, open=1.0, high=1.2, low=0.9, close=1.1)
    candles = bars(BREAKDOWN, *FALL, *RETURN)
    assert setup_from("EURUSD=X", "EUR/USD", candles, [green]) is None
    s = setup_from("EURUSD=X", "EUR/USD", candles, [red])
    assert (s.plan.direction, s.plan.entry, s.plan.stop, s.plan.target) == ("down", 97.2, 102.2, 82.2)
    assert s.key == "dropreturn:15m:EURUSD=X:1970-01-01T00:00:00+00:00"


def setup(symbol):
    return DropReturnSetup(
        symbol=symbol, name=None, direction="down", zone_bottom=1, zone_top=2, zone_start_time="2026-10-20T04:00:00+00:00",
        price=0.9, breakout_time="2026-10-29T04:00:00+00:00", return_time="2026-10-29T06:00:00+00:00",
        time="2026-10-29T06:15:00+00:00", key=f"k:{symbol}",
    )


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[SentAlert.__table__])
    monkeypatch.setattr(drop_return_scan.telegram, "is_configured", lambda: True)
    monkeypatch.setattr(drop_return_scan.settings, "telegram_penny_bot_token", "token")
    monkeypatch.setattr(drop_return_scan, "SEND_INTERVAL_SECONDS", 0)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_each_setup_is_sent_once(monkeypatch, db):
    monkeypatch.setattr(drop_return_scan, "scan_drop_returns", lambda: (20, [setup("AAA"), setup("BBB")]))
    send = Mock(return_value=1)
    monkeypatch.setattr(drop_return_scan.telegram, "post_message", send)

    assert run_drop_return_alerts(db).sent_count == 2
    assert run_drop_return_alerts(db).sent_count == 0
    assert send.call_count == 2
    assert send.call_args.kwargs["bot_token"] == "token"


def test_message_is_a_short_and_an_example_says_so():
    s = setup("EURUSD=X")
    assert drop_return_scan.format_message(s).startswith("🔴 שורט · <b>EURUSD=X</b>\n")
    assert drop_return_scan.format_message(s, example=True).endswith("🕘 דוגמה מההיסטוריה · 29/10 08:30")
