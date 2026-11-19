from unittest.mock import Mock

import pytest

from app import push, signal_log, telegram


@pytest.fixture(autouse=True)
def no_signal_log(monkeypatch):
    """A test's sent alert doesn't land on the real database's signals page."""
    monkeypatch.setattr(signal_log, "record", Mock())


@pytest.fixture(autouse=True)
def no_push(monkeypatch):
    """Nor does it reach the phones with the app's notifications on."""
    monkeypatch.setattr(push, "send", Mock())


@pytest.fixture(autouse=True)
def no_exchange_lookup(monkeypatch):
    """A test's TradingView link doesn't ask Yahoo for the stock's exchange or touch
    the real cache file."""
    monkeypatch.setattr(telegram, "_exchanges", {})
    monkeypatch.setattr(telegram, "_yahoo_exchange", Mock(return_value=None))
