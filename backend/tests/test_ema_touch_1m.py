from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import ema_touch_1m, mt5_trader, telegram
from app.candles import Candle
from app.config import settings
from app.db import Base
from app.ema_touch_1m import TouchSignal, find_signal, format_message, look, picture, run_signals
from app.models import SentAlert

EMA = 1.1
# (open, high, low, close): + over the EMA, - under it; touching it, 0 and r close the
# ways that count (green over it, red under it), g green under it, d red over it.
SHAPES = {
    "+": (1.101, 1.102, 1.101, 1.102),
    "-": (1.099, 1.099, 1.098, 1.098),
    "0": (1.099, 1.101, 1.099, 1.101),
    "r": (1.101, 1.101, 1.099, 1.099),
    "g": (1.099, 1.101, 1.099, 1.0995),
    "d": (1.101, 1.101, 1.099, 1.1005),
}


def candles(sides: str) -> list[Candle]:
    return [Candle(i * 60, *SHAPES[s]) for i, s in enumerate(sides)]


def found(sides: str):
    return find_signal(candles(sides), [EMA] * len(sides))


def test_a_green_close_over_the_ema_is_a_long_a_red_one_under_it_a_short():
    assert found("+" * 30 + "0") == ("clean", "up", 30)
    assert found("-" * 45 + "r") == ("clean", "down", 45)


def test_also_when_it_crossed_the_ema():
    assert found("-" * 30 + "0") == ("clean", "up", 30)
    assert found("+" * 30 + "r") == ("clean", "down", 30)
    assert found("+" * 20 + "0" + "-" * 5 + "0") == ("retest", "up", 5)


def test_no_signal_on_the_wrong_colour_or_side():
    assert found("+" * 30 + "g") is None
    assert found("-" * 30 + "d") is None


def test_a_touch_29_candles_after_the_last_one_isnt_clean():
    assert found("0" + "+" * 29 + "0") is None


def test_only_the_first_candle_on_the_ema():
    assert found("+" * 30 + "00") is None
    assert found("+" * 31) is None


def test_a_retest_comes_back_to_the_ema_after_a_break():
    assert found("+" * 20 + "r" + "-" * 5 + "r") == ("retest", "down", 5)
    assert found("-" * 20 + "00" + "+" * 12 + "0") == ("retest", "up", 12)


def test_a_retest_needs_time_away():
    assert found("+" * 20 + "0" + "-" * 4 + "r") is None


def test_a_slow_break_is_no_retest():
    assert found("+" * 20 + "0000" + "-" * 5 + "r") is None


def test_a_retest_comes_from_the_other_side():
    assert found("-" * 20 + "0" + "-" * 5 + "r") is None
    assert found("+-" * 10 + "0" + "-" * 5 + "r") is None  # no side before it
    assert found("+" * 14 + "-" * 6 + "0" + "-" * 5 + "r") == ("retest", "down", 5)


def test_a_retest_needs_candles_before_the_break():
    assert found("+" * 19 + "0" + "-" * 5 + "r") is None


def test_the_caption_and_the_picture():
    c = candles("+" * 100 + "0")
    up = TouchSignal("EURUSD", "clean", "up", 100, c, [EMA] * len(c))
    assert format_message(up) == "⚡ <b>EURUSD</b> · לונג · נגיעה נקייה\nנר ירוק נסגר מעל EMA 50 · סגירה 1.101 · 100 נרות בלי נגיעה לפניו"
    assert picture(up).startswith(b"\x89PNG")
    c = candles("+" * 20 + "0" + "-" * 11 + "r")
    down = TouchSignal("EURUSD", "retest", "down", 11, c, [EMA] * len(c))
    assert format_message(down) == "⚡ <b>EURUSD</b> · שורט · שבירה וריטסט\nנר אדום נסגר מתחת ל-EMA 50 · סגירה 1.099 · 11 נרות בלי נגיעה לפניו"


def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def rate(c: Candle) -> dict:
    return dict(time=c.time, open=c.open, high=c.high, low=c.low, close=c.close, tick_volume=1)


def terminal(monkeypatch, sides: str, now: float):
    """A terminal with these candles, its clock at now, two hours off the real one."""
    monkeypatch.setattr(ema_touch_1m, "ema_series", lambda c, period: [EMA] * len(c))
    monkeypatch.setattr(ema_touch_1m, "server_now", lambda names: now)
    monkeypatch.setattr(ema_touch_1m, "broker_offset", lambda names: 7200)
    mt5 = SimpleNamespace(
        TIMEFRAME_M1=1,
        symbol_select=lambda name, on: True,
        copy_rates_from_pos=lambda name, tf, start, count: [rate(c) for c in candles(sides)],
    )
    monkeypatch.setattr(mt5_trader, "mt5", mt5)
    monkeypatch.setattr(mt5_trader, "connect", lambda: True)
    monkeypatch.setattr(mt5_trader, "broker_symbol", lambda symbol: "EURUSD")
    monkeypatch.setattr(settings, "telegram_scalp_bot_token", "token")


def test_sends_the_candle_that_just_closed_once(monkeypatch):
    terminal(monkeypatch, "+" * 60 + "0", now=61 * 60 + 2)
    sent = []
    monkeypatch.setattr(telegram, "send_photo", lambda png, caption, chat_id=None, bot_token=None: sent.append((caption, bot_token)) or True)
    db = session()
    assert run_signals(db) == 1
    assert run_signals(db) == 0  # the same candle, looked at again
    assert sent[0][1] == "token"
    assert "לונג" in sent[0][0]
    [row] = db.query(SentAlert).all()
    assert row.key == f"ema-touch-1m:{60 * 60 - 7200}"


def test_a_failed_send_goes_again_on_the_next_look(monkeypatch):
    terminal(monkeypatch, "+" * 60 + "0", now=61 * 60 + 2)
    answers = iter([False, True])
    monkeypatch.setattr(telegram, "send_photo", lambda *a, **k: next(answers))
    db = session()
    assert run_signals(db) == 0
    assert run_signals(db) == 1


def test_the_candle_still_forming_isnt_looked_at(monkeypatch):
    # The touch is the forming one: the candle before it closed clean over the EMA.
    terminal(monkeypatch, "+" * 60 + "0", now=60 * 60 + 30)
    assert look() is None


def test_a_candle_from_before_a_market_break_isnt_looked_at(monkeypatch):
    terminal(monkeypatch, "+" * 60 + "0", now=62 * 60 + 1)
    assert look() is None


def test_no_bot_no_look(monkeypatch):
    terminal(monkeypatch, "+" * 60 + "0", now=61 * 60 + 2)
    monkeypatch.setattr(settings, "telegram_scalp_bot_token", "")
    assert run_signals(session()) == 0
