"""OHLC candle data for the per-stock chart. Fetched on demand from Yahoo Finance;
not scheduled or persisted. 4h candles aren't a native Yahoo interval, so they're
built by resampling 1h bars."""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

VALID_CANDLE_TIMEFRAMES = ("5m", "15m", "30m", "1h", "4h", "1d", "1w")
DEFAULT_CANDLE_TIMEFRAME = "1h"
MAX_CANDLES = 500

# (yfinance interval, period) per requested timeframe — periods stay within
# Yahoo's lookback limits for each intraday interval while giving enough bars
# to fill a MAX_CANDLES-wide chart. "1h" only needs ~6mo to cover MAX_CANDLES
# bars (2y was pure fetch overhead); "4h" keeps a longer period since it's
# resampled down from 1h bars (4x fewer bars after resampling).
_FETCH_PARAMS = {
    "5m": ("5m", "60d"),
    "15m": ("15m", "60d"),
    "30m": ("30m", "60d"),
    "1h": ("1h", "6mo"),
    "4h": ("1h", "2y"),  # resampled to 4h below
    "1d": ("1d", "2y"),
    "1w": ("1wk", "10y"),
}

# Short-lived cache so switching timeframes/symbols back and forth (or
# reopening the chart) doesn't re-hit Yahoo every time; chart data doesn't
# need to be more real-time than this.
CACHE_TTL_SECONDS = 30
_cache: dict[tuple[str, str], tuple[float, list["Candle"]]] = {}
_cache_lock = threading.Lock()


@dataclass
class Candle:
    time: int  # unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: Optional[int] = None


def _to_candles(df: pd.DataFrame) -> list[Candle]:
    out: list[Candle] = []
    for ts, row in df.iterrows():
        if row[["Open", "High", "Low", "Close"]].isna().any():
            continue
        out.append(
            Candle(
                time=int(ts.timestamp()),
                open=round(float(row["Open"]), 5),
                high=round(float(row["High"]), 5),
                low=round(float(row["Low"]), 5),
                close=round(float(row["Close"]), 5),
                volume=int(row["Volume"]) if "Volume" in row and pd.notna(row["Volume"]) else None,
            )
        )
    return out


def get_4h_dataframe(symbol: str, period: Optional[str] = None) -> Optional[pd.DataFrame]:
    """Raw 4h OHLCV dataframe, built from 1h bars. Exposed separately from
    get_candles() so indicator scans (e.g. EMA50 touch) can work off the same
    bars without going through the Candle conversion. `period` overrides how
    far back the 1h bars go, for scans that don't need the chart's full 2y.

    Grouped sequentially within each trading day (first 4 hourly bars, then
    whatever remains) rather than resampled on calendar time. Yahoo only
    returns bars inside the trading session (e.g. 9:30-16:00 ET for US
    equities), so a midnight-aligned "4h" bucket splits that session at
    arbitrary points instead of real 4-hour groups — this instead matches how
    charting platforms build 4h candles from an exchange's session."""
    interval, default_period = _FETCH_PARAMS["4h"]
    try:
        df = yf.Ticker(symbol).history(interval=interval, period=period or default_period)
    except Exception:
        logger.warning("4h candle fetch failed for %s", symbol)
        return None

    if df is None or df.empty:
        return None

    day = df.index.date
    bar_in_day = pd.Series(range(len(df)), index=df.index).groupby(day).cumcount().to_numpy()
    chunk_idx = bar_in_day // 4

    # One vectorized aggregation rather than a Python loop over the groups — the scans
    # build this for thousands of symbols, and the loop cost ~8x the download itself.
    out = (
        df.assign(_time=df.index)
        .groupby([day, chunk_idx], sort=False)
        .agg(
            _time=("_time", "first"),
            Open=("Open", "first"),
            High=("High", "max"),
            Low=("Low", "min"),
            Close=("Close", "last"),
            Volume=("Volume", "sum"),
        )
        .set_index("_time")
    )
    out.index.name = df.index.name
    return out.dropna(subset=["Open", "High", "Low", "Close"])


def get_candles(symbol: str, timeframe: str = DEFAULT_CANDLE_TIMEFRAME) -> list[Candle]:
    if timeframe not in VALID_CANDLE_TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {VALID_CANDLE_TIMEFRAMES}")

    cache_key = (symbol, timeframe)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]

    if timeframe == "4h":
        df = get_4h_dataframe(symbol)
        candles = _to_candles(df)[-MAX_CANDLES:] if df is not None else []
    else:
        interval, period = _FETCH_PARAMS[timeframe]
        try:
            df = yf.Ticker(symbol).history(interval=interval, period=period)
        except Exception:
            logger.warning("Candle fetch failed for %s (%s)", symbol, timeframe)
            df = None
        candles = _to_candles(df)[-MAX_CANDLES:] if df is not None and not df.empty else []

    if candles:
        with _cache_lock:
            _cache[cache_key] = (now, candles)
    return candles
