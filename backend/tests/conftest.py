from unittest.mock import Mock

import pytest

from app import signal_log


@pytest.fixture(autouse=True)
def no_signal_log(monkeypatch):
    """A test's sent alert doesn't land on the real database's signals page."""
    monkeypatch.setattr(signal_log, "record", Mock())
