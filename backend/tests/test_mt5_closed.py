from types import SimpleNamespace

from app import mt5_closed
from app.candles import Candle
from app.mt5_closed import DEAL_REASON_SL, DEAL_REASON_TP, closed_trades, format_message, outcome, picture

CLIENT = 0  # closed by hand


def deal(position, entry, price, time, type_=1, reason=3, profit=0.0, magic=30312):
    return SimpleNamespace(
        position_id=position, magic=magic, symbol="EURUSD", type=type_, entry=entry, reason=reason,
        price=price, time=time, profit=profit, commission=-0.4, swap=0.0, fee=0.0,
    )


def test_the_outcome_from_the_closing_deal():
    assert outcome("down", 1.1000, 1.0960, 1.1020, DEAL_REASON_TP) == "target"
    assert outcome("down", 1.1000, 1.1021, 1.1020, DEAL_REASON_SL) == "stop"
    # The stop moved to the entry: closed by the stop, a pip of slippage from the entry.
    assert outcome("down", 1.1000, 1.1001, 1.1020, DEAL_REASON_SL) == "breakeven"
    assert outcome("up", 1.1000, 1.1005, 1.0980, CLIENT) == "closed"


def test_a_position_opened_and_closed_in_the_deals():
    deals = [
        deal(7, 0, 1.1000, 600, type_=1),
        deal(7, 1, 1.1021, 1200, type_=0, reason=DEAL_REASON_SL, profit=-10.0),
        deal(8, 0, 1.2000, 900),  # still open
        deal(9, 0, 1.3000, 300, magic=50505),  # BOT MO's
        deal(9, 1, 1.3010, 400, magic=50505),
    ]
    orders = {7: SimpleNamespace(sl=1.1020, tp=1.0960)}
    [t] = closed_trades(deals, orders)
    assert (t.position, t.direction, t.entry, t.exit, t.stop, t.target) == (7, "down", 1.1000, 1.1021, 1.1020, 1.0960)
    assert (t.opened, t.closed, t.outcome) == (600, 1200, "stop")
    assert round(t.profit, 2) == -10.8  # with both deals' commission


def test_the_caption():
    [t] = closed_trades(
        [deal(7, 0, 1.1, 600, type_=0), deal(7, 1, 1.104, 1200, reason=DEAL_REASON_TP, profit=20.0)],
        {7: SimpleNamespace(sl=1.098, tp=1.104)},
    )
    assert format_message(t) == "✅ טייק פרופיט · <b>EURUSD</b> · לונג\nכניסה 1.1 · יציאה 1.104 · רווח 19.20$"


def test_the_picture_from_the_entry_candle_to_the_exit_one():
    [t] = closed_trades(
        [deal(7, 0, 1.1, 30_000, type_=0), deal(7, 1, 1.098, 31_000, reason=DEAL_REASON_SL)],
        {7: SimpleNamespace(sl=1.098, tp=1.104)},
    )
    t.candles = [Candle(i * 300, 1.1, 1.101, 1.099, 1.1) for i in range(120)]
    assert picture(t).startswith(b"\x89PNG")
    t.candles = t.candles[:50]  # its exit candle missing — text only
    assert picture(t) is None


def test_off_without_the_nasdaq_bot(monkeypatch):
    monkeypatch.setattr(mt5_closed.settings, "telegram_nasdaq_bot_token", "")
    assert mt5_closed.report_closed(db=None) == 0
