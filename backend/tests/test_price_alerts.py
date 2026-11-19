from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import price_alerts, push
from app.candles import Candle
from app.db import Base
from app.models import PriceAlert

SET_AT = datetime(2026, 11, 13, 10, 0)  # UTC
T0 = int(SET_AT.replace(tzinfo=timezone.utc).timestamp())


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def bar(t, low, high, close):
    return Candle(time=t, open=close, high=high, low=low, close=close)


def alert(price, above, set_at=SET_AT):
    return PriceAlert(symbol="EURUSD=X", price=price, above=int(above), created_at=set_at)


def test_above_reached_by_the_last_price():
    assert price_alerts.reached(alert(1.10, True), [bar(T0 + 300, 1.09, 1.095, 1.10)])


def test_above_reached_by_a_spike_after_it_was_set():
    candles = [bar(T0 + 300, 1.09, 1.105, 1.09), bar(T0 + 600, 1.08, 1.09, 1.085)]
    assert price_alerts.reached(alert(1.10, True), candles)


def test_a_spike_before_it_was_set_does_not_count():
    candles = [bar(T0 - 300, 1.09, 1.12, 1.09), bar(T0 + 300, 1.08, 1.09, 1.085)]
    assert not price_alerts.reached(alert(1.10, True), candles)


def test_below_reached_on_the_way_down():
    candles = [bar(T0 + 300, 1.07, 1.09, 1.085)]
    assert price_alerts.reached(alert(1.075, False), candles)
    assert not price_alerts.reached(alert(1.06, False), candles)


def test_create_sets_the_side_by_the_price_then(db, monkeypatch):
    monkeypatch.setattr(price_alerts, "get_candles", Mock(return_value=[bar(T0, 1.0, 1.1, 1.05)]))
    assert price_alerts.create(db, "EURUSD=X", 1.08).above == 1
    assert price_alerts.create(db, "EURUSD=X", 1.02).above == 0


def test_create_twice_keeps_one(db, monkeypatch):
    monkeypatch.setattr(price_alerts, "get_candles", Mock(return_value=[bar(T0, 1.0, 1.1, 1.05)]))
    first = price_alerts.create(db, "EURUSD=X", 1.08)
    assert price_alerts.create(db, "EURUSD=X", 1.08).id == first.id
    assert len(price_alerts.waiting(db)) == 1


def test_create_without_candles_raises(db, monkeypatch):
    monkeypatch.setattr(price_alerts, "get_candles", Mock(return_value=[]))
    with pytest.raises(price_alerts.NoPrice):
        price_alerts.create(db, "NOPE", 1.0)


def test_check_notifies_once_and_marks_it_done(db, monkeypatch):
    db.add(alert(1.10, True))
    db.add(alert(1.20, True))
    db.commit()
    monkeypatch.setattr(price_alerts, "get_candles", Mock(return_value=[bar(T0 + 300, 1.09, 1.11, 1.10)]))
    price_alerts.check(db, now=T0 + 400)
    assert push.send.call_count == 1
    assert push.send.call_args.args[0] == "🔔 EURUSD ⬆️ 1.1"
    assert [a.price for a in price_alerts.waiting(db)] == [1.20]
    price_alerts.check(db, now=T0 + 460)
    assert push.send.call_count == 1
