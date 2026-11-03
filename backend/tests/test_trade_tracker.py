from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import alert_chart, trade_tracker
from app.candles import Candle
from app.db import Base
from app.models import TrackedTrade
from app.trade_plan import TradePlan

# Wednesday 28/10/26 12:00 UTC — the forex market is open.
NOW = int(datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc).timestamp())
SHORT = TradePlan("down", entry=100, stop=101, target=97)
DRAW_PICTURE = trade_tracker.outcome_picture  # the real one — every test replaces it


def bar(t, low, high):
    return Candle(time=t, open=(low + high) / 2, high=high, low=low, close=(low + high) / 2)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[TrackedTrade.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def no_picture(monkeypatch):
    monkeypatch.setattr(trade_tracker, "outcome_picture", Mock(return_value=None))


def watch(db, plan=SHORT, symbol="EURUSD=X"):
    trade_tracker.track(f"k:{symbol}", symbol, "EUR/USD", "main", "1", 77, plan, now=NOW, db=db)
    return db.query(TrackedTrade).filter_by(key=f"k:{symbol}").one()


def test_first_level_reached_wins_and_a_bar_reaching_both_is_the_stop(db):
    t = watch(db)
    assert t.checked_from == NOW
    assert trade_tracker.outcome(t, [bar(NOW, 99, 100.5), bar(NOW + 300, 96.5, 99)]) == ("target", NOW + 300)
    assert trade_tracker.outcome(t, [bar(NOW, 99, 101.2), bar(NOW + 300, 96.5, 99)]) == ("stop", NOW)
    assert trade_tracker.outcome(t, [bar(NOW, 96.5, 101.2)]) == ("stop", NOW)
    assert trade_tracker.outcome(t, [bar(NOW - 300, 96, 102), bar(NOW, 99, 100)]) is None  # before the alert


def test_hit_replies_to_the_alert_once(monkeypatch, db):
    t = watch(db)
    post = Mock(return_value=5)
    monkeypatch.setattr(trade_tracker.telegram, "post_message", post)
    fetch = lambda symbol, age: [bar(NOW, 99, 100.5), bar(NOW + 300, 96.8, 99)]
    assert trade_tracker.check_trades(db, NOW + 600, fetch) == 1
    assert t.outcome == "target"
    assert post.call_args.kwargs["reply_to"] == 77
    assert "טייק פרופיט" in post.call_args.args[0]
    assert trade_tracker.check_trades(db, NOW + 900, fetch) == 0


def test_hit_replies_with_the_chart_picture_when_there_is_one(monkeypatch, db):
    t = watch(db)
    monkeypatch.setattr(trade_tracker, "outcome_picture", Mock(return_value=b"png"))
    photo = Mock(return_value=5)
    monkeypatch.setattr(trade_tracker.telegram, "post_photo", photo)
    fetch = lambda symbol, age: [bar(NOW, 99, 100.5), bar(NOW + 300, 100, 101.5)]
    assert trade_tracker.check_trades(db, NOW + 600, fetch) == 1
    trade_tracker.outcome_picture.assert_called_once_with(t, NOW + 300)
    assert photo.call_args.args[0] == b"png"
    assert "סטופ לוס" in photo.call_args.args[1]
    assert photo.call_args.kwargs["reply_to"] == 77


def test_picture_candles_fit_the_trade():
    assert trade_tracker.picture_timeframe("^NDX", NOW, NOW + 20 * 60) == "1m"
    assert trade_tracker.picture_timeframe("EURUSD=X", NOW, NOW + 20 * 60) == "5m"
    assert trade_tracker.picture_timeframe("EURUSD=X", NOW, NOW + 2 * 86400) == "1h"
    assert trade_tracker.picture_timeframe("AAPL", NOW, NOW + 60 * 86400) == "1d"


def test_outcome_picture_draws_the_trade_up_to_the_hit(monkeypatch, db):
    t = watch(db)
    t.outcome = "target"
    bars = [bar(NOW + (i - 40) * 300, 99.5 - i * 0.05, 100.5 - i * 0.05) for i in range(60)]
    monkeypatch.setattr(trade_tracker, "_picture_candles", Mock(return_value=bars))
    render = Mock(return_value=b"png")
    monkeypatch.setattr(trade_tracker.alert_chart, "render", render)
    assert DRAW_PICTURE(t, NOW + 15 * 300) == b"png"
    candles = render.call_args.args[0]
    assert candles[-1].time == NOW + 15 * 300  # nothing after the hit
    assert render.call_args.kwargs["plan_from"] == datetime.fromtimestamp(NOW, timezone.utc).isoformat()
    assert render.call_args.kwargs["closed"] == "target"


def test_outcome_chart_renders():
    bars = [bar(NOW + i * 300, 99.5 - i * 0.05, 100.5 - i * 0.05) for i in range(80)]
    plan_from = datetime.fromtimestamp(NOW + 60 * 300, timezone.utc).isoformat()
    png = alert_chart.render(bars, "EUR/USD · 5m · Target hit", None, None, None, plan=SHORT,
                             plan_from=plan_from, closed="target")
    assert png.startswith(b"\x89PNG")


def test_open_trade_moves_on_to_the_last_bar(monkeypatch, db):
    t = watch(db)
    fetch = lambda symbol, age: [bar(NOW, 99, 100.5), bar(NOW + 300, 98, 100.2)]
    assert trade_tracker.check_trades(db, NOW + 600, fetch) == 0
    assert (t.outcome, t.checked_from) == (None, NOW + 300)


def test_failed_reply_is_retried(monkeypatch, db):
    t = watch(db)
    fetch = lambda symbol, age: [bar(NOW, 101, 102)]
    monkeypatch.setattr(trade_tracker.telegram, "post_message", Mock(return_value=None))
    assert trade_tracker.check_trades(db, NOW + 600, fetch) == 0
    assert t.outcome is None
    monkeypatch.setattr(trade_tracker.telegram, "post_message", Mock(return_value=5))
    assert trade_tracker.check_trades(db, NOW + 600, fetch) == 1
    assert t.outcome == "stop"


def test_stocks_are_only_checked_in_the_us_session(db):
    watch(db, symbol="AAPL")
    fetch = Mock(return_value=[])
    trade_tracker.check_trades(db, NOW, fetch)  # 8:00 New York
    fetch.assert_not_called()
    trade_tracker.check_trades(db, NOW + 3 * 3600, fetch)  # 11:00
    fetch.assert_called_once()


def test_forex_is_not_checked_on_the_weekend():
    saturday = int(datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc).timestamp())
    assert trade_tracker.in_session("EURUSD=X", NOW)
    assert not trade_tracker.in_session("EURUSD=X", saturday)
