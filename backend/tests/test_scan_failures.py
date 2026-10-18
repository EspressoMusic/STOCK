from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import scanner
from app.db import get_db
from app.main import app


@pytest.fixture(autouse=True)
def no_live_fallback(monkeypatch):
    monkeypatch.setattr(scanner, "_nasdaq_quotes", Mock(side_effect=scanner.MarketDataUnavailable("offline")))


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


def test_nasdaq_fallback_preserves_mover_filters_and_sort(monkeypatch):
    monkeypatch.setattr(scanner.yf, "screen", Mock(side_effect=RuntimeError("HTTP 401")))
    def quote(symbol, change, volume=200_000, price=5):
        return {"symbol": symbol, "shortName": symbol, "regularMarketPrice": price,
                "regularMarketChangePercent": change, "regularMarketVolume": volume}
    monkeypatch.setattr(scanner, "_nasdaq_quotes", lambda: [
        quote("AAA", -10), quote("BBB", -20), quote("CCC", 15),
        quote("DDD", -2), quote("EEE", -30, volume=1),
        quote("FFF", -40, price=50), quote("ABCDW", -50),
    ])
    result = scanner.scan_nasdaq("losers", max_price=10)
    assert [c.symbol for c in result.candidates] == ["BBB", "AAA"]
    assert result.total_matches == 2
    assert [c.symbol for c in scanner.scan_nasdaq("gainers").candidates] == ["CCC"]


def test_fallback_does_not_drop_unsupported_filters(monkeypatch):
    monkeypatch.setattr(scanner.yf, "screen", Mock(side_effect=RuntimeError("HTTP 401")))
    for run in (scanner.scan_broken_stocks, lambda: scanner.scan_nasdaq("losers", sector="Technology")):
        with pytest.raises(scanner.MarketDataUnavailable):
            run()


def test_nasdaq_number_parsing():
    assert scanner._number("$1,234.50") == 1234.5
    assert scanner._number("-12.5%") == -12.5
    for value in (None, "N/A", "", "NaN", "Infinity"):
        assert scanner._number(value) is None


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
        assert "detail" in response.json()
        db.add.assert_not_called()
        db.commit.assert_not_called()
        enrich.assert_not_called()
        ai.assert_not_called()
    finally:
        app.dependency_overrides.clear()
