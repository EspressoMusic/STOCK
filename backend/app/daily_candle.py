"""The daily-candle filter the alerts share (retest_alerts.py, zone_touch_scan.py):
an alert goes out only when the daily chart agrees with the trade's direction.

The user's rule (October 2026): the last closed daily candle is in the trade's colour —
red for a trade down, green for one up — or one of the LOOKBACK_DAYS before it was, and
no daily candle since has closed past its body against the trade (above the open of a
red one, below the open of a green one). Wicks past the body don't count against it,
and neither do closes further in the trade's direction.

Daily candles are built from 1h bars, on the forex day that rolls over at 17:00 New
York. Yahoo's own daily forex bars don't line up — a day often opens far from the
previous day's close — so their colours can't be trusted. A stock's session (9:30-16:00
New York) sits inside one such day, so its candles come out as its trading days."""
import logging
from datetime import timedelta
from typing import Optional, Sequence

import pandas as pd
import yfinance as yf

from .candles import Candle, _to_candles

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 3  # the last closed candle and the two before it
# 1h bars this far back cover LOOKBACK_DAYS trading days even across a weekend.
HISTORY_PERIOD = "1mo"
FOREX_TZ = "America/New_York"
# 17:00 New York plus this is the next midnight, so the forex day is the shifted date.
_DAY_ROLL = pd.Timedelta(hours=7)


def _forex_day(ts: pd.Timestamp):
    day = (ts.tz_convert(FOREX_TZ) + _DAY_ROLL).date()
    # Yahoo has an hour or so after Friday's 17:00 close; it's still Friday's day.
    return day - timedelta(days=1) if day.weekday() == 5 else day


def daily_candles(hourly: pd.DataFrame) -> list[Candle]:
    """Closed forex-day candles from 1h bars, oldest first; the day still trading is left out."""
    if hourly is None or hourly.empty:
        return []
    hourly = hourly[hourly.index >= hourly.index[-1] - pd.Timedelta(days=14)]
    day = [_forex_day(ts) for ts in hourly.index]
    today = (pd.Timestamp.now(tz=FOREX_TZ) + _DAY_ROLL).date()
    out = (
        hourly.assign(_time=hourly.index, _day=day)
        .groupby("_day", sort=True)
        .agg(
            _time=("_time", "first"),
            Open=("Open", "first"),
            High=("High", "max"),
            Low=("Low", "min"),
            Close=("Close", "last"),
            Volume=("Volume", "sum"),
        )
    )
    out = out[out.index < today].set_index("_time")
    return _to_candles(out)


def fetch_daily_candles(symbol: str) -> Optional[list[Candle]]:
    try:
        df = yf.Ticker(symbol).history(interval="1h", period=HISTORY_PERIOD)
    except Exception:
        logger.warning("Daily candle fetch failed for %s", symbol)
        return None
    return daily_candles(df)


def confirms(daily: Sequence[Candle], direction: str) -> bool:
    """Whether the daily candles agree with a trade "up" or "down" (see the module docstring)."""
    down = direction == "down"
    n = len(daily)
    for k in range(1, min(LOOKBACK_DAYS, n) + 1):
        c = daily[n - k]
        if not (c.close < c.open if down else c.close > c.open):
            continue
        if all(d.close <= c.open if down else d.close >= c.open for d in daily[n - k + 1 :]):
            return True
    return False
