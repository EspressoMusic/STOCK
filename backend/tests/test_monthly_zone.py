from datetime import datetime
from zoneinfo import ZoneInfo

from app import monthly_zone
from app.candles import Candle
from tests.test_weekly_zone import CLOSES, ENTRY, k_with, weeks

NY = ZoneInfo("America/New_York")


def test_the_weekly_rules_on_monthly_candles_with_their_own_key():
    candles = weeks(CLOSES)  # the spacing doesn't matter to the rules
    k = k_with(len(candles), 10)
    s = monthly_zone._signal("LLY", None, candles, k, k, ENTRY)
    assert s.direction == "up" and s.plan.entry == 104.5
    assert s.key == f"monthly:LLY:{candles[ENTRY].time}"


def test_a_month_is_closed_once_the_next_one_starts():
    october = Candle(time=int(datetime(2026, 10, 1, tzinfo=NY).timestamp()), open=1, high=1, low=1, close=1, volume=1)
    november = datetime(2026, 11, 1, tzinfo=NY).timestamp()
    assert not monthly_zone.month_closed(october, november - 60)
    assert monthly_zone.month_closed(october, november)
    december = Candle(time=int(datetime(2026, 12, 1, tzinfo=NY).timestamp()), open=1, high=1, low=1, close=1, volume=1)
    assert monthly_zone.month_closed(december, datetime(2027, 1, 1, tzinfo=NY).timestamp())
    assert not monthly_zone.month_closed(december, datetime(2026, 12, 31, 23, tzinfo=NY).timestamp())
