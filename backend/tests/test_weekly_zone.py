from datetime import datetime
from zoneinfo import ZoneInfo

from app import weekly_zone
from app.candles import Candle

NY = ZoneInfo("America/New_York")
MONDAY = int(datetime(2025, 1, 6, tzinfo=NY).timestamp())
WEEK = 7 * 86400

# Three dips to 100, each pushed back up to 112 — a support zone at 100 with 3 touches; then
# back down to it, a red week and a green one closing over its body: the long's entry.
DIP = [112, 108, 104, 100.5, 104, 108]
CLOSES = [112, 112, 112] + DIP * 3 + [112, 108, 104, 101, 104.5]
RED, ENTRY = len(CLOSES) - 2, len(CLOSES) - 1


def weeks(closes):
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        out.append(Candle(time=MONDAY + i * WEEK, open=o, high=max(o, c) + 0.5, low=min(o, c) - 0.5, close=c, volume=1000))
    return out


def k_with(n, entry_value):
    k = [50.0] * n
    k[-1] = entry_value
    return k


def test_long_on_the_week_closing_over_the_red_body_at_the_zone_when_oversold():
    candles = weeks(CLOSES)
    direction, zone, a, trade = weekly_zone.signal_at(candles, k_with(len(candles), 10), ENTRY)
    assert (direction, a) == ("up", RED)
    assert zone.bottom < 100 < zone.top and len(zone.touch_idx) >= 3
    assert trade.entry == 104.5
    assert trade.stop < 100  # under the zone and the visit's wicks
    assert trade.target == 104.5 + 3 * (104.5 - trade.stop)


def test_no_signal_unless_the_stochastic_rsi_is_oversold():
    candles = weeks(CLOSES)
    assert weekly_zone.signal_at(candles, k_with(len(candles), 50), ENTRY) is None


def test_short_mirrors_it_at_resistance_when_overbought():
    candles = weeks([200 - c for c in CLOSES])
    direction, zone, a, trade = weekly_zone.signal_at(candles, k_with(len(candles), 90), ENTRY)
    assert (direction, a) == ("down", RED)
    assert trade.stop > 100
    assert weekly_zone.signal_at(candles, k_with(len(candles), 10), ENTRY) is None


def test_a_week_is_closed_at_fridays_close():
    [c] = weeks([100])
    friday_close = datetime(2025, 1, 10, 16, tzinfo=NY).timestamp()
    assert not weekly_zone.week_closed(c, friday_close - 60)
    assert weekly_zone.week_closed(c, friday_close)
