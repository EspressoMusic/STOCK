from types import SimpleNamespace

from app import alert_chart, mt5_closed, mt5_trader, strong_zone_1m
from app.candles import Candle
from app.mt5_closed import DEAL_REASON_SL, DEAL_REASON_TP, closed_trades, format_message, outcome, picture, setup_marks
from app.zones import Zone

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
    assert format_message(t) == "✅ טייק פרופיט · <b>EURUSD</b> · לונג\nאסטרטגיה: EMA 50 + 3 נגיעות\nכניסה 1.1 · יציאה 1.104 · רווח 19.20$"
    t.magic = mt5_trader.MAGIC
    assert "אסטרטגיה: פורקס המשך\n" in format_message(t)


def test_the_picture_from_the_entry_candle_to_the_exit_one():
    [t] = closed_trades(
        [deal(7, 0, 1.1, 30_000, type_=0), deal(7, 1, 1.098, 31_000, reason=DEAL_REASON_SL)],
        {7: SimpleNamespace(sl=1.098, tp=1.104)},
    )
    t.candles = [Candle(i * 300, 1.1, 1.101, 1.099, 1.1) for i in range(120)]
    assert picture(t).startswith(b"\x89PNG")
    t.candles = t.candles[:50]  # its exit candle missing — text only
    assert picture(t) is None


def test_the_setup_from_the_candles_closed_when_it_opened(monkeypatch):
    # Opened at 60_005, a few seconds into candle 200: the entry candle is 199.
    [t] = closed_trades(
        [deal(7, 0, 1.1, 60_005, type_=0), deal(7, 1, 1.098, 63_000, reason=DEAL_REASON_SL)],
        {7: SimpleNamespace(sl=1.098, tp=1.104)},
    )
    t.candles = [Candle(i * 300, 1.1, 1.101, 1.099, 1.1) for i in range(220)]
    seen = {}

    def found(window, ranging):
        seen.update(first=window[0].time, last=window[-1].time, ranging=ranging)
        return "up", Zone(id="z", bottom=1.099, top=1.0995, unit=0.001, start_idx=150, touch_idx=[150, 170]), 185

    monkeypatch.setattr(mt5_closed, "find_setup", found)
    marks = setup_marks(t, t.candles)
    assert seen == {"first": 8 * 300, "last": 199 * 300, "ranging": False}  # the strategy's 192 candles
    assert (marks["zone_bottom"], marks["zone_top"]) == (1.099, 1.0995)
    # The two touches before and the third visit's first candle; the entry candle gets the arrow.
    assert marks["touches"] == [mt5_closed._iso(i * 300) for i in (158, 178, 193)]
    assert marks["breakout"] == mt5_closed._iso(199 * 300)
    assert picture(t).startswith(b"\x89PNG")

    monkeypatch.setattr(mt5_closed, "find_setup", lambda w, ranging: None)
    assert setup_marks(t, t.candles)["zone_bottom"] is None  # the setup gone: the trade only
    t.magic = 50505
    monkeypatch.setattr(mt5_closed, "find_setup", found)
    assert setup_marks(t, t.candles)["zone_bottom"] is None  # an alert's trade


def test_off_without_the_nasdaq_bot(monkeypatch):
    monkeypatch.setattr(mt5_closed.settings, "telegram_nasdaq_bot_token", "")
    assert mt5_closed.report_closed(db=None) == 0


def strong_trade():
    [t] = closed_trades(
        [deal(7, 0, 1.1, 6_005, type_=0, magic=strong_zone_1m.MAGIC), deal(7, 1, 1.098, 6_250, reason=DEAL_REASON_SL, magic=strong_zone_1m.MAGIC)],
        {7: SimpleNamespace(sl=1.098, tp=1.106)},
    )
    t.candles = [Candle(i * 60, 1.1, 1.101, 1.099, 1.1) for i in range(120)]
    t.zones = [Zone(id="z", bottom=1.099, top=1.0995, unit=0.001, start_idx=0, touch_idx=[0, 1, 2])]
    return t


def test_a_strong_zones_setup_from_its_one_minute_candles(monkeypatch):
    t = strong_trade()
    seen = {}
    monkeypatch.setattr(strong_zone_1m, "find_setup", lambda window, zones: seen.update(last=window[-1].time) or ("up", zones[0], 95))
    marks = setup_marks(t, t.candles)
    assert seen == {"last": 99 * 60}  # opened a few seconds into candle 100
    assert (marks["zone_bottom"], marks["zone_top"]) == (1.099, 1.0995)
    assert marks["zone_start"] == mt5_closed._iso(0)  # across the picture
    assert marks["touches"] == [mt5_closed._iso(95 * 60)]
    assert marks["breakout"] == mt5_closed._iso(99 * 60)
    monkeypatch.setattr(strong_zone_1m, "find_setup", lambda window, zones: ("up", zones[0], 99))
    assert setup_marks(t, t.candles)["touches"] == []  # the entry candle reached it: only the arrow
    monkeypatch.setattr(strong_zone_1m, "find_setup", lambda window, zones: ("down", zones[0], 95))
    assert setup_marks(t, t.candles)["zone_bottom"] is None  # another trade's setup


def test_a_strong_zones_picture_from_the_entry_minute_to_the_exit_one(monkeypatch):
    t = strong_trade()
    monkeypatch.setattr(strong_zone_1m, "find_setup", lambda window, zones: None)
    titles = []
    render = alert_chart.render
    monkeypatch.setattr(alert_chart, "render", lambda candles, title, **kw: titles.append((title, candles[-1].time)) or render(candles, title, **kw))
    assert picture(t).startswith(b"\x89PNG")
    assert titles == [("EURUSD · 1m · Stop hit", 104 * 60)]  # closed at 6_250, in candle 104
