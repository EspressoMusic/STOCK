from app.symbol_analysis import Request, parse_request, tradingview_symbol


def test_pairs_by_any_spelling():
    assert parse_request("EURUSD") == Request("EURUSD=X", "EUR/USD", "4h")
    assert parse_request("eur/usd") == Request("EURUSD=X", "EUR/USD", "4h")
    assert parse_request("GBPCHF=X") == Request("GBPCHF=X", "GBP/CHF", "4h")
    assert parse_request("usdils") == Request("USDILS=X", "USD/ILS", "4h")


def test_stocks_coins_and_aliases():
    assert parse_request("aapl") == Request("AAPL", "AAPL", "4h")
    assert parse_request("BRK-B") == Request("BRK-B", "BRK-B", "4h")
    assert parse_request("btc") == Request("BTC-USD", "BTC", "4h")
    assert parse_request("ETH/USD") == Request("ETH-USD", "ETH", "4h")
    assert parse_request("nas100") == Request("^NDX", "NAS100", "4h")


def test_timeframe_either_side():
    assert parse_request("EURUSD 1h") == Request("EURUSD=X", "EUR/USD", "1h")
    assert parse_request("1D aapl") == Request("AAPL", "AAPL", "1d")
    assert parse_request("AAPL יומי") == Request("AAPL", "AAPL", "1d")


def test_other_messages_are_not_symbols():
    assert parse_request("מה המצב") is None
    assert parse_request("סטטוס") is None
    assert parse_request("AAPL MSFT") is None
    assert parse_request("AAPL 1h 4h") is None
    assert parse_request("") is None


def test_tradingview_symbols():
    assert tradingview_symbol("BTC-USD") == "BTCUSD"
    assert tradingview_symbol("^NDX") == "NDX"
    assert tradingview_symbol("BRK-B") == "BRK-B"
