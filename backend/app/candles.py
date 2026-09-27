"""OHLC candle data for the per-stock chart. Fetched on demand from Yahoo Finance;
not scheduled or persisted. 4h candles aren't a native Yahoo interval, so they're
built by resampling 1h bars."""
import logging
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
# to fill a MAX_CANDLES-wide chart.
_FETCH_PARAMS = {
    "5m": ("5m", "60d"),
    "15m": ("15m", "60d"),
    "30m": ("30m", "60d"),
    "1h": ("1h", "2y"),
    "4h": ("1h", "2y"),  # resampled to 4h below
    "1d": ("1d", "2y"),
    "1w": ("1wk", "10y"),
}


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


def get_4h_dataframe(symbol: str) -> Optional[pd.DataFrame]:
    """Raw 4h OHLCV dataframe, resampled from 1h bars. Exposed separately from
    get_candles() so indicator scans (e.g. EMA50 touch) can work off the same
    resampled bars without going through the Candle conversion."""
    interval, period = _FETCH_PARAMS["4h"]
    try:
        df = yf.Ticker(symbol).history(interval=interval, period=period)
    except Exception:
        logger.warning("4h candle fetch failed for %s", symbol)
        return None

    if df is None or df.empty:
        return None

    return df.resample("4h", origin="start_day").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Open", "High", "Low", "Close"])


def get_candles(symbol: str, timeframe: str = DEFAULT_CANDLE_TIMEFRAME) -> list[Candle]:
    if timeframe not in VALID_CANDLE_TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {VALID_CANDLE_TIMEFRAMES}")

    if timeframe == "4h":
        df = get_4h_dataframe(symbol)
        return _to_candles(df)[-MAX_CANDLES:] if df is not None else []

    interval, period = _FETCH_PARAMS[timeframe]
    try:
        df = yf.Ticker(symbol).history(interval=interval, period=period)
    except Exception:
        logger.warning("Candle fetch failed for %s (%s)", symbol, timeframe)
        return []

    if df is None or df.empty:
        return []

    return _to_candles(df)[-MAX_CANDLES:]
