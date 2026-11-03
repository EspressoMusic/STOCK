from types import SimpleNamespace

import pytest

from app import mt5_trader
from app.config import settings
from app.trade_plan import TradePlan

LONG = TradePlan("up", entry=1.1000, stop=1.0950, target=1.1150)
SHORT = TradePlan("down", entry=1.1000, stop=1.1050, target=1.0850)


class FakeMT5:
    """The MetaTrader5 package's calls the trader makes, on a 10,000 USD account and
    EURUSD at 1.1000/1.1001 — 100,000 units a lot."""

    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_REAL = 0, 2
    TRADE_ACTION_DEAL, ORDER_TIME_GTC = 1, 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self, demo=True, positions=(), names=("EURUSD",)):
        self.demo, self.positions, self.names = demo, list(positions), names
        self.sent = []

    def initialize(self, **kw):
        return True

    def last_error(self):
        return (1, "Success")

    def terminal_info(self):
        return SimpleNamespace()

    def account_info(self):
        mode = self.ACCOUNT_TRADE_MODE_DEMO if self.demo else self.ACCOUNT_TRADE_MODE_REAL
        return SimpleNamespace(login=123, server="Broker-Demo", trade_mode=mode, balance=10_000.0, currency="USD")

    def symbol_info(self, name):
        if name not in self.names:
            return None
        return SimpleNamespace(name=name, volume_step=0.01, volume_min=0.01, volume_max=100.0, digits=5, filling_mode=2)

    def symbols_get(self, group=""):
        return [SimpleNamespace(name=n) for n in self.names if n.startswith(group.rstrip("*"))]

    def symbol_select(self, name, enable):
        return True

    def positions_get(self, symbol=None):
        return [p for p in self.positions if p.symbol == symbol]

    def symbol_info_tick(self, name):
        return SimpleNamespace(bid=1.1000, ask=1.1001)

    def order_calc_profit(self, order_type, name, lots, price_open, price_close):
        sign = 1 if order_type == self.ORDER_TYPE_BUY else -1
        return (price_close - price_open) * 100_000 * lots * sign

    def order_send(self, request):
        self.sent.append(request)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=777, price=request["price"], comment="done")


@pytest.fixture
def fake(monkeypatch):
    fake = FakeMT5()
    monkeypatch.setattr(mt5_trader, "mt5", fake)
    monkeypatch.setattr(mt5_trader, "_connected", False)
    monkeypatch.setattr(mt5_trader, "_symbols", {})
    monkeypatch.setattr(settings, "mt5_mode", "trade")
    monkeypatch.setattr(settings, "mt5_allow_real", False)
    monkeypatch.setattr(settings, "mt5_bots", "main,1h")
    monkeypatch.setattr(settings, "mt5_risk_percent", 0.5)
    monkeypatch.setattr(settings, "mt5_symbol_suffix", "")
    return fake


def test_lots_for_risk_rounds_down_to_the_step():
    assert mt5_trader.lots_for_risk(50, 510, 0.01, 0.01, 100) == 0.09
    assert mt5_trader.lots_for_risk(5, 510, 0.01, 0.01, 100) == 0.0  # less than the smallest lot
    assert mt5_trader.lots_for_risk(1e9, 510, 0.01, 0.01, 100) == 100
    assert mt5_trader.lots_for_risk(50, 0, 0.01, 0.01, 100) == 0.0


def test_levels_ok():
    assert mt5_trader.levels_ok("up", 1.1000, 1.1001, 1.0950, 1.1150)
    assert not mt5_trader.levels_ok("up", 1.0940, 1.0941, 1.0950, 1.1150)  # past the stop
    assert not mt5_trader.levels_ok("up", 1.1160, 1.1161, 1.0950, 1.1150)  # past the target
    assert mt5_trader.levels_ok("down", 1.1000, 1.1001, 1.1050, 1.0850)
    assert not mt5_trader.levels_ok("down", 1.1060, 1.1061, 1.1050, 1.0850)


def test_opens_a_long_sized_by_the_risk(fake):
    assert mt5_trader.open_trade("1h", "EURUSD=X", LONG) == 777
    (req,) = fake.sent
    # 0.5% of 10,000 = 50; one lot loses (1.1001 - 1.0950) * 100,000 = 510 at the stop.
    assert req["volume"] == 0.09
    assert req["type"] == fake.ORDER_TYPE_BUY and req["price"] == 1.1001
    assert (req["sl"], req["tp"]) == (1.095, 1.115)
    assert req["magic"] == mt5_trader.MAGIC and req["type_filling"] == fake.ORDER_FILLING_IOC


def test_opens_a_short_at_the_bid(fake):
    assert mt5_trader.open_trade("main", "EURUSD=X", SHORT) == 777
    assert fake.sent[0]["type"] == fake.ORDER_TYPE_SELL and fake.sent[0]["price"] == 1.1000


def test_finds_the_brokers_symbol_name(fake):
    fake.names = ("EURUSD.m",)
    assert mt5_trader.open_trade("1h", "EURUSD=X", LONG) == 777
    assert fake.sent[0]["symbol"] == "EURUSD.m"


def test_waits_for_a_new_symbols_first_quote(fake, monkeypatch):
    quotes = iter([SimpleNamespace(bid=0.0, ask=0.0), SimpleNamespace(bid=1.1000, ask=1.1001)])
    monkeypatch.setattr(fake, "symbol_info_tick", lambda name: next(quotes))
    monkeypatch.setattr(mt5_trader.time, "sleep", lambda s: None)
    assert mt5_trader.open_trade("1h", "EURUSD=X", LONG) == 777


def test_not_a_real_account_unless_allowed(fake, monkeypatch):
    fake.demo = False
    assert mt5_trader.open_trade("1h", "EURUSD=X", LONG) is None
    monkeypatch.setattr(settings, "mt5_allow_real", True)
    assert mt5_trader.open_trade("1h", "EURUSD=X", LONG) == 777


def test_one_trade_per_pair(fake):
    fake.positions = [SimpleNamespace(symbol="EURUSD", magic=mt5_trader.MAGIC)]
    assert mt5_trader.open_trade("1h", "EURUSD=X", LONG) is None
    fake.positions = [SimpleNamespace(symbol="EURUSD", magic=0)]  # one opened by hand
    assert mt5_trader.open_trade("1h", "EURUSD=X", LONG) == 777


def test_only_the_set_bots_forex_pairs_and_modes(fake, monkeypatch):
    assert mt5_trader.open_trade("stocks", "EURUSD=X", LONG) is None
    assert mt5_trader.open_trade("1h", "AAPL", LONG) is None
    monkeypatch.setattr(settings, "mt5_mode", "off")
    assert mt5_trader.open_trade("1h", "EURUSD=X", LONG) is None
    monkeypatch.setattr(settings, "mt5_mode", "paper")
    assert mt5_trader.open_trade("1h", "EURUSD=X", LONG) == 0
    assert fake.sent == []


def test_never_raises(fake, monkeypatch):
    monkeypatch.setattr(fake, "symbol_info_tick", lambda name: 1 / 0)
    assert mt5_trader.open_trade("1h", "EURUSD=X", LONG) is None
