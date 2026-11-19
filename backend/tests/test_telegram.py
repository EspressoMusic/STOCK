from unittest.mock import Mock

from app import telegram
from app.telegram import tradingview_url

CHART = "https://www.tradingview.com/chart/?symbol="


def test_a_stock_opens_with_its_exchange_in_front(monkeypatch, tmp_path):
    monkeypatch.setattr(telegram, "EXCHANGE_CACHE", tmp_path / "exchanges.json")
    monkeypatch.setattr(telegram, "_yahoo_exchange", Mock(side_effect=lambda s: {"AAPL": "NMS", "BRK-B": "NYQ"}[s]))
    assert tradingview_url("AAPL") == CHART + "NASDAQ%3AAAPL"
    assert tradingview_url("BRK-B") == CHART + "NYSE%3ABRK.B"


def test_an_exchange_is_asked_once_and_kept(monkeypatch, tmp_path):
    cache = tmp_path / "exchanges.json"
    monkeypatch.setattr(telegram, "EXCHANGE_CACHE", cache)
    yahoo = Mock(return_value="NMS")
    monkeypatch.setattr(telegram, "_yahoo_exchange", yahoo)
    tradingview_url("AAPL")
    tradingview_url("AAPL")
    assert yahoo.call_count == 1
    assert '"AAPL": "NASDAQ"' in cache.read_text()


def test_a_list_takes_only_the_kept_exchanges(monkeypatch):
    monkeypatch.setattr(telegram, "_exchanges", {"AAPL": "NASDAQ"})
    assert tradingview_url("AAPL", lookup=False) == CHART + "NASDAQ%3AAAPL"
    assert tradingview_url("TSLA", lookup=False) == CHART + "TSLA"
    telegram._yahoo_exchange.assert_not_called()


def test_an_unknown_exchange_leaves_the_bare_symbol():
    assert tradingview_url("ABCD") == CHART + "ABCD"


def test_forex_and_tel_aviv_need_no_lookup():
    assert tradingview_url("GBPCHF=X") == CHART + "OANDA%3AGBPCHF"
    assert tradingview_url("ICHO.TA") == CHART + "TASE%3AICHO"
    telegram._yahoo_exchange.assert_not_called()
