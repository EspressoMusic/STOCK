from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import alert_chart, monthly_ema, telegram, trade_tracker
from app.candles import Candle
from app.db import Base
from app.models import SentAlert
from app.monthly_ema import Signal, direction_at, find_signals, format_message, picture, run_monthly_ema, signal_at, split_weeks

EMA = 100.0


@pytest.fixture(autouse=True)
def no_weeks(monkeypatch):
    monkeypatch.setattr(monthly_ema, "fetch", lambda *args, **kwargs: [])


def month(i: int) -> int:
    return int(datetime(2020 + i // 12, i % 12 + 1, 1, tzinfo=timezone.utc).timestamp())


def candle(open: float, high: float, low: float, close: float, i: int = 60) -> Candle:
    return Candle(month(i), open, high, low, close, 1)


GREEN_OVER = candle(101, 110, 99, 105)
RED_UNDER = candle(99, 101, 90, 95)


def history(last: Candle, count: int = 60) -> list[Candle]:
    quiet = [candle(120, 121, 119, 120, i) for i in range(count - 1)]
    return [*quiet, last]


def test_a_green_candle_touching_and_closing_over_the_line_is_a_long():
    assert direction_at(GREEN_OVER, EMA) == "up"


def test_a_red_candle_touching_and_closing_under_the_line_is_a_short():
    assert direction_at(RED_UNDER, EMA) == "down"


def test_the_wrong_colour_for_the_side_is_no_signal():
    assert direction_at(candle(106, 110, 99, 105), EMA) is None  # red, over the line
    assert direction_at(candle(94, 101, 90, 95), EMA) is None  # green, under the line


def test_no_touch_is_no_signal():
    assert direction_at(candle(101, 110, 100.5, 105), EMA) is None
    assert direction_at(candle(99, 99.5, 90, 95), EMA) is None


def test_closing_on_the_line_is_no_signal():
    assert direction_at(candle(99, 101, 98, 100), EMA) is None


def test_the_stop_is_behind_the_candle_and_the_target_three_times_the_risk():
    candles = history(GREEN_OVER)
    s = signal_at("AAPL", None, candles, [EMA] * len(candles), len(candles) - 1)
    assert s.direction == "up" and s.plan.entry == 105
    assert s.plan.stop < 99
    assert s.plan.target == 105 + 3 * (105 - s.plan.stop)

    candles = history(RED_UNDER)
    s = signal_at("AAPL", None, candles, [EMA] * len(candles), len(candles) - 1)
    assert s.direction == "down" and s.plan.stop > 101


def test_needs_50_months():
    candles = history(GREEN_OVER, 49)
    assert signal_at("AAPL", None, candles, [EMA] * 49, 48) is None
    candles = history(GREEN_OVER, 50)
    assert signal_at("AAPL", None, candles, [EMA] * 50, 49) is not None


def test_a_short_whose_target_is_under_zero_is_dropped():
    candles = history(candle(99, 160, 90, 95))
    assert signal_at("AAPL", None, candles, [EMA] * len(candles), len(candles) - 1) is None


def signal(symbol: str = "AAPL", last: Candle = GREEN_OVER) -> Signal:
    candles = history(last)
    return signal_at(symbol, None, candles, [EMA] * len(candles), len(candles) - 1)


def test_once_a_month_per_stock_whichever_way():
    assert signal().key == "ema50:AAPL:2025-01"
    assert signal(last=RED_UNDER).key == signal().key
    assert signal("MSFT").key != signal().key


def test_the_month_is_read_at_midday_so_a_stamp_before_midnight_stays_in_it():
    late = Candle(month(60) - 3600, 101, 110, 99, 105, 1)
    assert signal(last=late).key == "ema50:AAPL:2025-01"


def test_the_closest_to_the_line_first():
    near = signal("NEAR", candle(100, 110, 99, 101))
    far = signal("FAR")
    assert near.distance < far.distance


def test_the_caption_and_the_picture():
    assert format_message(signal()).startswith("🟢 לונג · <b>AAPL</b> · EMA 50 חודשי")
    assert format_message(signal(last=RED_UNDER)).startswith("🔴 שורט · <b>AAPL</b> · EMA 50 חודשי")
    assert "דוגמה" in format_message(signal(), example=True)
    assert picture(signal()).startswith(b"\x89PNG")


def test_an_example_shows_the_months_after_it(monkeypatch):
    monkeypatch.setattr(monthly_ema, "ema_series", lambda c, period: [EMA] * len(c))
    candles = [candle(120, 121, 119, 120, i) for i in range(60)]
    candles[55] = candle(101, 110, 99, 105, 55)
    [s] = find_signals("AAPL", None, candles)
    assert len(s.candles) == 56 and len(s.after) == 4 and len(s.emas) == 60
    assert s.distance == 5 / 105
    assert picture(s).startswith(b"\x89PNG")


def weeks_around() -> list[Candle]:
    """Mondays from a month before the signal's month (01/25) to a month after it."""
    first = int(datetime(2024, 12, 2, tzinfo=timezone.utc).timestamp())
    return [Candle(first + i * 7 * 86400, 104, 106, 103, 105, 1) for i in range(13)]


def test_the_weeks_split_after_the_last_of_the_signals_month():
    weeks = weeks_around()
    before, later = split_weeks(weeks, signal())
    assert before + later == weeks
    assert f"{datetime.fromtimestamp(before[-1].time, timezone.utc):%d/%m/%y}" == "27/01/25"
    assert f"{datetime.fromtimestamp(later[0].time, timezone.utc):%d/%m/%y}" == "03/02/25"


def panels(monkeypatch) -> list:
    drawn = []
    monkeypatch.setattr(alert_chart, "_png", lambda charts, size, divider=False, widths=None: drawn.extend(charts) or b"\x89PNG")
    return drawn


def test_the_picture_is_the_month_and_the_week_with_the_entry_highlighted_and_no_trade(monkeypatch):
    monkeypatch.setattr(monthly_ema, "fetch", lambda symbol, interval: weeks_around())
    drawn = panels(monkeypatch)
    picture(signal())
    (month, month_marks), (week, week_marks) = drawn
    assert month[1] == "AAPL · 1M · EMA 50" and week[1] == "AAPL · 1W"
    assert month[2:4] == week[2:4] == (GREEN_OVER.low, GREEN_OVER.high)
    assert "plan" not in month_marks and "plan" not in week_marks
    assert week_marks["breakout"] == monthly_ema._iso(week[0][-1].time)
    assert week_marks["after"] == weeks_around()[9:]
    assert month_marks["line"] and "line" not in week_marks


def test_no_weeks_leaves_the_month_alone(monkeypatch):
    drawn = panels(monkeypatch)
    picture(signal())
    [(month, _)] = drawn
    assert month[1] == "AAPL · 1M · EMA 50"


def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_a_signal_goes_to_the_stocks_bot_once_and_its_trade_is_not_watched(monkeypatch):
    monkeypatch.setattr(monthly_ema, "scan", lambda: [signal()])
    monkeypatch.setattr(monthly_ema.telegram, "bot_chat", lambda bot: (f"token-{bot}", "chat"))
    sent, tracked, opened = [], [], []
    monkeypatch.setattr(telegram, "post_photo", lambda png, text, chat_id=None, bot_token=None: sent.append(bot_token) or 7)
    monkeypatch.setattr(trade_tracker, "track", lambda *args: tracked.append(args))
    monkeypatch.setattr(trade_tracker.mt5_trader, "open_trade", lambda *args: opened.append(args))
    db = session()
    assert run_monthly_ema(db) == 1
    assert run_monthly_ema(db) == 0  # the next Friday, the same month
    assert sent == ["token-stocks"]
    assert tracked == [] and opened == []
    [row] = db.query(SentAlert).all()
    assert row.key == "ema50:AAPL:2025-01"
