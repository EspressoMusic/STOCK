from datetime import date, datetime

import pandas as pd
import pytest

from app import model_challenge as mc


def candles(rows, start="2026-01-05"):
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=pd.bdate_range(start, periods=len(rows)))


# Yesterday's range is 10, so with k = 0.3 the orders sit 3 above and below today's open of 100.
YESTERDAY = (100, 105, 95, 100)


def day_return(today, cost=0.0):
    return mc.breakout_returns(candles([YESTERDAY, today]), k=0.3, cost=cost).iloc[-1]


def test_the_buy_order_is_held_to_the_close():
    assert day_return((100, 108, 99, 106)) == pytest.approx(106 / 103 - 1)


def test_the_sell_order_is_a_short_to_the_close():
    assert day_return((100, 101, 92, 94)) == pytest.approx(97 / 94 - 1)


def test_a_day_inside_the_orders_is_no_trade_and_no_cost():
    assert day_return((100, 102, 98, 101), cost=0.01) == 0


def test_both_orders_reached_is_the_loss_between_them():
    assert day_return((100, 104, 96, 100)) == pytest.approx(-6 / 103)


def test_a_trade_pays_the_cost_in_and_out():
    assert day_return((100, 108, 99, 106), cost=0.001) == pytest.approx(106 / 103 - 1 - 0.002)


def test_the_first_day_has_no_orders():
    assert mc.breakout_returns(candles([YESTERDAY]), k=0.3, cost=0.0).iloc[0] == 0


def test_todays_candle_is_left_out_until_new_york_closes():
    df = candles([YESTERDAY, YESTERDAY], start="2026-11-10")  # Tuesday and Wednesday
    during = datetime(2026, 11, 11, 12, 0, tzinfo=mc.NEW_YORK)
    after = datetime(2026, 11, 11, 16, 30, tzinfo=mc.NEW_YORK)
    assert len(mc.whole_days(df, during)) == 1
    assert len(mc.whole_days(df, after)) == 2
    assert len(mc.whole_days(df, datetime(2026, 11, 12, 9, 0, tzinfo=mc.NEW_YORK))) == 2


def test_evaluate_marks_the_months_and_the_pass(monkeypatch):
    # Every day opens at 100 and climbs to close at 104: bought at 101.8, a small win a day.
    days = pd.bdate_range("2025-01-01", "2026-03-10")
    df = pd.DataFrame({"Open": 100.0, "High": 105.0, "Low": 99.0, "Close": 104.0}, index=days)
    monkeypatch.setattr(mc, "TEST_FROM", "2025-06")
    entry = mc.Entry(
        id="x", model="X", picked_at=date(2026, 1, 15), asset="", timeframe="", strategy="", rules=(), note="",
        symbol="A", k=0.3, share=0.5, cost=0.0,
    )
    out = mc.evaluate(entry, df, today=date(2026, 3, 10))
    assert out["passed"] and out["test"]["months"] == 9 and out["test"]["positive"] == 9  # June to February
    whole_days = len(days[(days >= "2025-06-01") & (days < "2026-03-01")])
    assert out["trades"] == {"count": whole_days, "wins": whole_days}
    assert out["next_distance"] == 1.8
    assert [m["month"] for m in out["months"] if m["live"]] == ["2026-01", "2026-02", "2026-03"]
    assert [m["month"] for m in out["months"] if m["running"]] == ["2026-03"]
    assert out["since_pick"]["days"] == len(days[days >= "2026-01-15"])
    one_day = (104 / 101.8 - 1) * 0.5  # half the account in the trade
    assert out["since_pick"]["total"] == pytest.approx(((1 + one_day) ** out["since_pick"]["days"] - 1) * 100, abs=0.01)
