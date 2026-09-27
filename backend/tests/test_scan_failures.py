from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import scanner
from app.db import get_db
from app.main import app


@pytest.mark.parametrize("scan", [
    lambda: scanner.scan_nasdaq("losers"),
    lambda: scanner.scan_nasdaq("gainers"),
    scanner.scan_broken_stocks,
    lambda: scanner.scan_by_cap("large"),
    lambda: scanner.scan_by_cap("small"),
])
def test_provider_failure_is_not_an_empty_scan(monkeypatch, scan):
    monkeypatch.setattr(scanner.yf, "screen", Mock(side_effect=RuntimeError("HTTP 401")))
    with pytest.raises(scanner.MarketDataUnavailable):
        scan()


@pytest.mark.parametrize("payload", [None, {}, {"quotes": None}])
def test_invalid_response_is_not_an_empty_scan(monkeypatch, payload):
    monkeypatch.setattr(scanner.yf, "screen", Mock(return_value=payload))
    with pytest.raises(scanner.MarketDataUnavailable):
        scanner.scan_nasdaq("losers")


def test_legitimate_empty_result_is_allowed(monkeypatch):
    monkeypatch.setattr(scanner.yf, "screen", Mock(return_value={"quotes": [], "total": 0}))
    assert scanner.scan_nasdaq("losers").total_matches == 0


def test_later_page_failure_does_not_return_partial_success(monkeypatch):
    monkeypatch.setattr(scanner.yf, "screen", Mock(side_effect=[
        {"quotes": [{"symbol": "TEST"}] * 250, "total": 300},
        RuntimeError("HTTP 401"),
    ]))
    with pytest.raises(scanner.MarketDataUnavailable):
        scanner.scan_nasdaq("losers")


def test_failed_scan_returns_503_without_saving_or_spending(monkeypatch):
    from app import pipeline

    monkeypatch.setattr(scanner.yf, "screen", Mock(side_effect=RuntimeError("HTTP 401")))
    enrich = Mock()
    ai = Mock()
    monkeypatch.setattr(pipeline, "enrich_symbols", enrich)
    monkeypatch.setattr(pipeline, "_generate_outlooks", ai)
    db = Mock()
    app.dependency_overrides[get_db] = lambda: db
    try:
        # No lifespan: never start scheduled scans in a unit test.
        client = TestClient(app)
        response = client.post("/api/scans/run", json={"direction": "losers"})
        assert response.status_code == 503
        assert "Yahoo Finance" in response.json()["detail"]
        db.add.assert_not_called()
        db.commit.assert_not_called()
        enrich.assert_not_called()
        ai.assert_not_called()
    finally:
        app.dependency_overrides.clear()
