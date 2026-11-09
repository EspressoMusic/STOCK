from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import alert_chart, mt5_closed, mt5_opened, mt5_trader, strong_zone_1m, zone_touch_scan
from app.candles import Candle
from app.db import Base
from app.models import SentAlert
from app.mt5_opened import format_message, opened_trades, picture
from app.zones import Zone


def position(ticket, time, type_=0, magic=30312, sl=1.098, tp=1.104):
    return SimpleNamespace(ticket=ticket, magic=magic, symbol="EURUSD", type=type_, price_open=1.1, sl=sl, tp=tp, time=time)


def test_our_open_positions_oldest_first():
    trades = opened_trades([position(8, 900, type_=1), position(7, 600), position(9, 300, magic=50505)])  # BOT MO's
    assert [(t.position, t.direction, t.opened) for t in trades] == [(7, "up", 600), (8, "down", 900)]
    assert (trades[0].entry, trades[0].stop, trades[0].target) == (1.1, 1.098, 1.104)
    assert trades[0].key == "mt5-opened:7"


def test_the_caption():
    [t] = opened_trades([position(7, 600)])
    assert format_message(t) == "🟢 נכנס לעסקה · <b>EURUSD</b> · לונג\nאסטרטגיה: EMA 50 + 3 נגיעות\nכניסה 1.1 · סטופ 1.098 · יעד 1.104"
    [t] = opened_trades([position(7, 600, type_=1, sl=0.0, tp=0.0, magic=mt5_trader.MAGIC)])
    assert format_message(t) == "🟢 נכנס לעסקה · <b>EURUSD</b> · שורט\nאסטרטגיה: פורקס המשך\nכניסה 1.1"


def test_an_alerts_trade_is_drawn_without_a_setup():
    [t] = opened_trades([position(7, 60_005, magic=mt5_trader.MAGIC)])
    t.candles = [Candle(i * 300, 1.1, 1.101, 1.099, 1.1) for i in range(220)]
    assert picture(t).startswith(b"\x89PNG")


def test_the_picture_up_to_the_entry_candle(monkeypatch):
    # Opened at 60_005, a few seconds into candle 200: the entry candle is 199.
    [t] = opened_trades([position(7, 60_005)])
    t.candles = [Candle(i * 300, 1.1, 1.101, 1.099, 1.1) for i in range(220)]
    seen = {}

    def found(window, ranging):
        seen.update(last=window[-1].time)
        return "up", Zone(id="z", bottom=1.099, top=1.0995, unit=0.001, start_idx=150, touch_idx=[150, 170]), 185

    monkeypatch.setattr(mt5_closed, "find_setup", found)
    assert picture(t).startswith(b"\x89PNG")
    assert seen == {"last": 199 * 300}
    t.candles = []  # no candles — text only
    assert picture(t) is None


def test_each_trade_is_sent_once(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[SentAlert.__table__])
    session = sessionmaker(bind=engine)()
    positions = [position(7, 600)]
    terminal = SimpleNamespace(positions_get=lambda: positions)
    sent = []
    monkeypatch.setattr(mt5_opened.mt5_trader, "mt5", terminal)
    monkeypatch.setattr(mt5_opened.mt5_trader, "connect", lambda: True)
    monkeypatch.setattr(mt5_opened.settings, "mt5_mode", "trade")
    monkeypatch.setattr(mt5_opened.settings, "telegram_nasdaq_bot_token", "token")
    monkeypatch.setattr(mt5_opened, "broker_offset", lambda names: 7200)
    monkeypatch.setattr(mt5_opened, "candles_from", lambda name, since, offset, magic: [])
    monkeypatch.setattr(mt5_opened, "send", lambda t: sent.append((t.position, t.opened)) or True)
    monkeypatch.setattr(zone_touch_scan, "SEND_INTERVAL_SECONDS", 0)

    assert mt5_opened.report_opened(session) == 1
    assert mt5_opened.report_opened(session) == 0
    assert sent == [(7, 600 - 7200)]  # on the real clock

    positions.append(position(8, 900))
    assert mt5_opened.report_opened(session) == 1
    assert sent[-1][0] == 8
    session.close()


def test_off_without_the_nasdaq_bot(monkeypatch):
    monkeypatch.setattr(mt5_opened.settings, "telegram_nasdaq_bot_token", "")
    assert mt5_opened.report_opened(db=None) == 0


def test_a_strong_zones_trade_on_one_minute_candles_with_its_zone(monkeypatch):
    # Opened at 12_005, a few seconds into candle 200: the entry candle is 199.
    [t] = opened_trades([position(7, 12_005, magic=strong_zone_1m.MAGIC)])
    t.candles = [Candle(i * 60, 1.1, 1.101, 1.099, 1.1) for i in range(220)]
    t.zones = [Zone(id="z", bottom=1.099, top=1.0995, unit=0.001, start_idx=0, touch_idx=[0, 1, 2])]
    seen = {}

    def found(window, zones):
        seen.update(last=window[-1].time, zones=zones)
        return "up", zones[0], 195

    monkeypatch.setattr(strong_zone_1m, "find_setup", found)
    titles = []
    render = alert_chart.render
    monkeypatch.setattr(alert_chart, "render", lambda candles, title, **kw: titles.append((title, candles[-1].time, kw["zone_bottom"], kw["touches"])) or render(candles, title, **kw))
    assert picture(t).startswith(b"\x89PNG")
    assert seen == {"last": 199 * 60, "zones": t.zones}
    assert titles == [("EURUSD · 1m · Entry", 199 * 60, 1.099, [mt5_closed._iso(195 * 60)])]
    assert format_message(t).splitlines()[1] == "אסטרטגיה: אזורים חזקים + נר דקה"
