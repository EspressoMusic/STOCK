"""On-demand technical scan (not scheduled, not persisted): looks across liquid Nasdaq
stocks plus a fixed set of major forex pairs for 5- or 15-minute candles where Stochastic
RSI is overbought/oversold AND the same candle's [low, high] range is touching (or nearly
touching) the EMA50 — a confluence some traders use as a reversal/reaction signal."""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import yfinance as yf

from .candles import get_4h_dataframe
from .config import settings
from .scanner import NASDAQ_EXCHANGES, _fetch_all_quotes, _is_derivative
from yfinance import EquityQuery

logger = logging.getLogger(__name__)

VALID_TIMEFRAMES = ("5m", "15m")
DEFAULT_TIMEFRAME = "15m"
HISTORY_PERIOD = "5d"
RSI_PERIOD = 14
STOCH_PERIOD = 14
K_SMOOTH = 3
D_SMOOTH = 3
MIN_BARS = RSI_PERIOD + STOCH_PERIOD + K_SMOOTH + D_SMOOTH  # enough history for the indicator chain to be defined

FOREX_PAIRS = [
    ("EURUSD=X", "EUR/USD"),
    ("GBPUSD=X", "GBP/USD"),
    ("USDJPY=X", "USD/JPY"),
    ("USDCHF=X", "USD/CHF"),
    ("AUDUSD=X", "AUD/USD"),
    ("USDCAD=X", "USD/CAD"),
    ("NZDUSD=X", "NZD/USD"),
    ("EURGBP=X", "EUR/GBP"),
    ("EURJPY=X", "EUR/JPY"),
    ("GBPJPY=X", "GBP/JPY"),
    ("EURCHF=X", "EUR/CHF"),
    ("AUDJPY=X", "AUD/JPY"),
    ("USDCNH=X", "USD/CNH"),
    ("USDMXN=X", "USD/MXN"),
    ("USDZAR=X", "USD/ZAR"),
]


@dataclass
class TechnicalCandidate:
    symbol: str
    name: Optional[str]
    asset_class: str  # "stock" | "forex"
    price: float
    signal: str  # "overbought" | "oversold"
    stoch_k: float
    stoch_d: float
    ema: float
    ema_period: int
    timeframe: str


@dataclass
class TechnicalScanResult:
    timeframe: str
    scanned_at: str
    universe_stock_count: int
    universe_forex_count: int
    stocks: list[TechnicalCandidate]
    forex: list[TechnicalCandidate]


EMA_BREAKOUT_TIMEFRAME = "1h"
EMA_BREAKOUT_HISTORY_PERIOD = "1mo"
EMA_BREAKOUT_MIN_BARS = 60  # enough 1h bars for EMA50 to have settled, plus the breakout/confirmation pair


@dataclass
class EmaBreakoutCandidate:
    symbol: str
    name: Optional[str]
    price: float
    ema: float
    ema_period: int
    breakout_time: str
    confirmation_time: str


@dataclass
class EmaBreakoutScanResult:
    scanned_at: str
    universe_stock_count: int
    stocks: list[EmaBreakoutCandidate]


EMA_TOUCH_4H_MIN_BARS = 60  # enough resampled 4h bars for EMA50 to have settled


@dataclass
class EmaTouchCandidate:
    symbol: str
    name: Optional[str]
    price: float
    ema: float
    ema_period: int
    candle_time: str


@dataclass
class EmaTouch4hScanResult:
    scanned_at: str
    universe_stock_count: int
    stocks: list[EmaTouchCandidate]


def _get_stock_universe(limit: int) -> list[tuple[str, Optional[str]]]:
    """Liquid Nasdaq stocks, ranked by trading volume — a cheap pre-filter so the
    expensive per-symbol candle fetch below only runs on names worth watching."""
    query = EquityQuery(
        "and",
        [
            EquityQuery("eq", ["region", "us"]),
            EquityQuery("is-in", ["exchange", *NASDAQ_EXCHANGES]),
            EquityQuery("gt", ["dayvolume", 300_000]),
        ],
    )
    try:
        quotes = _fetch_all_quotes(query, sort_field="dayvolume", sort_asc=False, fetch_cap=limit * 2)
    except Exception:
        logger.exception("Yahoo Finance screener request failed during technical-scan universe fetch")
        return []

    out: list[tuple[str, Optional[str]]] = []
    for q in quotes:
        symbol = q.get("symbol")
        name = q.get("shortName") or q.get("longName")
        if not symbol or _is_derivative(symbol, name):
            continue
        out.append((symbol, name))
        if len(out) >= limit:
            break
    return out


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _stoch_rsi(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    r = _rsi(close)
    min_r = r.rolling(STOCH_PERIOD).min()
    max_r = r.rolling(STOCH_PERIOD).max()
    raw = (r - min_r) / (max_r - min_r) * 100
    k = raw.rolling(K_SMOOTH).mean()
    d = k.rolling(D_SMOOTH).mean()
    return k, d


def _evaluate(symbol: str, name: Optional[str], asset_class: str, timeframe: str) -> Optional[TechnicalCandidate]:
    try:
        df = yf.Ticker(symbol).history(interval=timeframe, period=HISTORY_PERIOD)
    except Exception:
        logger.warning("Technical scan: history fetch failed for %s", symbol)
        return None

    if df is None or len(df) < MIN_BARS:
        return None

    close = df["Close"]
    k, d = _stoch_rsi(close)
    ema = close.ewm(span=settings.technical_ema_period, adjust=False).mean()

    last_k, last_d, last_ema = k.iloc[-1], d.iloc[-1], ema.iloc[-1]
    if pd.isna(last_k) or pd.isna(last_d) or pd.isna(last_ema):
        return None

    if last_k >= settings.technical_stochrsi_overbought:
        signal = "overbought"
    elif last_k <= settings.technical_stochrsi_oversold:
        signal = "oversold"
    else:
        return None

    last_low, last_high, last_close = df["Low"].iloc[-1], df["High"].iloc[-1], df["Close"].iloc[-1]
    # "Touching" = the EMA sits inside the candle's range, with a small tolerance for
    # near-misses just outside it (0.15% of price) since an exact touch is rare.
    tolerance = last_close * 0.0015
    touching = (last_ema >= last_low - tolerance) and (last_ema <= last_high + tolerance)
    if not touching:
        return None

    return TechnicalCandidate(
        symbol=symbol,
        name=name,
        asset_class=asset_class,
        price=round(float(last_close), 5 if asset_class == "forex" else 2),
        signal=signal,
        stoch_k=round(float(last_k), 1),
        stoch_d=round(float(last_d), 1),
        ema=round(float(last_ema), 5 if asset_class == "forex" else 2),
        ema_period=settings.technical_ema_period,
        timeframe=timeframe,
    )


def _evaluate_ema_breakout(symbol: str, name: Optional[str]) -> Optional[EmaBreakoutCandidate]:
    """Breakout candle = an hourly bar that opened below EMA50 and closed above it
    (crossed up through the EMA within that one candle). Confirmation candle = the
    very next hourly bar, which must stay entirely above the EMA (low above it too),
    i.e. the breakout held rather than getting swept back below on the next bar."""
    try:
        df = yf.Ticker(symbol).history(interval=EMA_BREAKOUT_TIMEFRAME, period=EMA_BREAKOUT_HISTORY_PERIOD)
    except Exception:
        logger.warning("EMA breakout scan: history fetch failed for %s", symbol)
        return None

    if df is None or len(df) < EMA_BREAKOUT_MIN_BARS:
        return None

    ema = df["Close"].ewm(span=settings.technical_ema_period, adjust=False).mean()

    breakout_open, breakout_close, breakout_ema = df["Open"].iloc[-2], df["Close"].iloc[-2], ema.iloc[-2]
    confirm_low, confirm_close, confirm_ema = df["Low"].iloc[-1], df["Close"].iloc[-1], ema.iloc[-1]
    if pd.isna(breakout_ema) or pd.isna(confirm_ema):
        return None

    broke_out = breakout_open < breakout_ema and breakout_close > breakout_ema
    held_above = confirm_low > confirm_ema and confirm_close > confirm_ema
    if not (broke_out and held_above):
        return None

    return EmaBreakoutCandidate(
        symbol=symbol,
        name=name,
        price=round(float(confirm_close), 2),
        ema=round(float(confirm_ema), 2),
        ema_period=settings.technical_ema_period,
        breakout_time=df.index[-2].to_pydatetime().astimezone(timezone.utc).isoformat(),
        confirmation_time=df.index[-1].to_pydatetime().astimezone(timezone.utc).isoformat(),
    )


def scan_ema50_breakout() -> EmaBreakoutScanResult:
    """On-demand scan (not scheduled, not persisted) for liquid Nasdaq stocks whose most
    recent hourly candle broke above EMA50 with the following candle holding above it."""
    stock_universe = _get_stock_universe(settings.technical_stock_universe_size)

    stocks: list[EmaBreakoutCandidate] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate_ema_breakout, sym, name) for sym, name in stock_universe]
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                stocks.append(result)

    stocks.sort(key=lambda c: c.confirmation_time, reverse=True)

    return EmaBreakoutScanResult(
        scanned_at=datetime.now(timezone.utc).isoformat(),
        universe_stock_count=len(stock_universe),
        stocks=stocks,
    )


def _evaluate_ema_touch_4h(symbol: str, name: Optional[str]) -> Optional[EmaTouchCandidate]:
    """Same "touching" logic as the intraday technical scan (EMA sits inside the candle's
    [low, high] range, with a small tolerance for near-misses), applied to the most recent
    4h candle instead of a 5m/15m one."""
    df = get_4h_dataframe(symbol)
    if df is None or len(df) < EMA_TOUCH_4H_MIN_BARS:
        return None

    ema = df["Close"].ewm(span=settings.technical_ema_period, adjust=False).mean()
    last_low, last_high, last_close, last_ema = (
        df["Low"].iloc[-1], df["High"].iloc[-1], df["Close"].iloc[-1], ema.iloc[-1]
    )
    if pd.isna(last_ema):
        return None

    tolerance = last_close * 0.0015
    touching = (last_ema >= last_low - tolerance) and (last_ema <= last_high + tolerance)
    if not touching:
        return None

    return EmaTouchCandidate(
        symbol=symbol,
        name=name,
        price=round(float(last_close), 2),
        ema=round(float(last_ema), 2),
        ema_period=settings.technical_ema_period,
        candle_time=df.index[-1].to_pydatetime().astimezone(timezone.utc).isoformat(),
    )


def scan_ema50_touch_4h() -> EmaTouch4hScanResult:
    """On-demand scan (not scheduled, not persisted) for liquid Nasdaq stocks whose most
    recent 4h candle touches (or nearly touches) EMA50."""
    stock_universe = _get_stock_universe(settings.technical_stock_universe_size)

    stocks: list[EmaTouchCandidate] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate_ema_touch_4h, sym, name) for sym, name in stock_universe]
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                stocks.append(result)

    stocks.sort(key=lambda c: c.candle_time, reverse=True)

    return EmaTouch4hScanResult(
        scanned_at=datetime.now(timezone.utc).isoformat(),
        universe_stock_count=len(stock_universe),
        stocks=stocks,
    )


EMA_DOUBLE_TOUCH_FOREX_TIMEFRAME = "1h"
EMA_DOUBLE_TOUCH_FOREX_HISTORY_PERIOD = "1mo"
EMA_DOUBLE_TOUCH_FOREX_MIN_BARS = 60  # enough 1h bars for EMA50 to have settled
EMA_DOUBLE_TOUCH_FOREX_LOOKBACK = 30  # how many recent hourly candles to search for a second touch
EMA_DOUBLE_TOUCH_FOREX_MAX_GAP = 5  # the latest touch must be within this many candles of "now" to still count as live
# Forex candle ranges are tight (a handful of pips), so the 0.15%-of-price tolerance used
# by the other EMA scans (tuned for stocks) is far too loose here — on a pair like
# USDCAD that's ~20 pips, wider than most hourly candles, so it "touches" during a clean
# trending move away from the average. 0.03% keeps it to a few pips either way.
EMA_DOUBLE_TOUCH_FOREX_TOLERANCE_PCT = 0.0003


@dataclass
class EmaDoubleTouchCandidate:
    symbol: str
    name: Optional[str]
    price: float
    ema: float
    ema_period: int
    first_touch_time: str
    second_touch_time: str


@dataclass
class EmaDoubleTouchForexScanResult:
    scanned_at: str
    universe_forex_count: int
    forex: list[EmaDoubleTouchCandidate]


def _evaluate_ema_double_touch_forex_1h(symbol: str, name: Optional[str]) -> Optional[EmaDoubleTouchCandidate]:
    """"Double touch" of EMA50: within a recent window of hourly candles, price touches
    (or nearly touches) EMA50, pulls away, then comes back and touches it again — two
    separate touch *events*, not just several consecutive candles hovering on the line
    (which would trivially "touch" a slow-moving average without any real retest).
    Consecutive touching candles are collapsed into one event; each event's first bar is
    reported as that touch's time. Tolerance is tighter than the other EMA scans' — see
    EMA_DOUBLE_TOUCH_FOREX_TOLERANCE_PCT."""
    try:
        df = yf.Ticker(symbol).history(
            interval=EMA_DOUBLE_TOUCH_FOREX_TIMEFRAME, period=EMA_DOUBLE_TOUCH_FOREX_HISTORY_PERIOD
        )
    except Exception:
        logger.warning("EMA double-touch scan: history fetch failed for %s", symbol)
        return None

    if df is None or len(df) < EMA_DOUBLE_TOUCH_FOREX_MIN_BARS:
        return None

    ema = df["Close"].ewm(span=settings.technical_ema_period, adjust=False).mean()

    window = df.iloc[-EMA_DOUBLE_TOUCH_FOREX_LOOKBACK:]
    window_ema = ema.iloc[-EMA_DOUBLE_TOUCH_FOREX_LOOKBACK:]

    tolerance = window["Close"] * EMA_DOUBLE_TOUCH_FOREX_TOLERANCE_PCT
    touching = (window_ema >= window["Low"] - tolerance) & (window_ema <= window["High"] + tolerance) & window_ema.notna()

    touch_events: list[tuple[int, int]] = []  # (start, end) bar positions of each run of consecutive touching candles
    event_start = None
    for i, is_touch in enumerate(touching):
        if is_touch and event_start is None:
            event_start = i
        elif not is_touch and event_start is not None:
            touch_events.append((event_start, i - 1))
            event_start = None
    if event_start is not None:
        touch_events.append((event_start, len(touching) - 1))

    if len(touch_events) < 2:
        return None

    last_position = len(touching) - 1
    if last_position - touch_events[-1][1] > EMA_DOUBLE_TOUCH_FOREX_MAX_GAP:
        return None  # most recent touch event is too stale to count as a live signal

    first_touch_pos, second_touch_pos = touch_events[-2][0], touch_events[-1][0]
    last_close, last_ema = df["Close"].iloc[-1], ema.iloc[-1]

    return EmaDoubleTouchCandidate(
        symbol=symbol,
        name=name,
        price=round(float(last_close), 5),
        ema=round(float(last_ema), 5),
        ema_period=settings.technical_ema_period,
        first_touch_time=window.index[first_touch_pos].to_pydatetime().astimezone(timezone.utc).isoformat(),
        second_touch_time=window.index[second_touch_pos].to_pydatetime().astimezone(timezone.utc).isoformat(),
    )


def scan_ema50_double_touch_forex_1h() -> EmaDoubleTouchForexScanResult:
    """On-demand scan (not scheduled, not persisted) for major forex pairs whose hourly
    candles have touched EMA50 at least twice within a recent lookback window."""
    forex: list[EmaDoubleTouchCandidate] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate_ema_double_touch_forex_1h, sym, label) for sym, label in FOREX_PAIRS]
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                forex.append(result)

    forex.sort(key=lambda c: c.second_touch_time, reverse=True)

    return EmaDoubleTouchForexScanResult(
        scanned_at=datetime.now(timezone.utc).isoformat(),
        universe_forex_count=len(FOREX_PAIRS),
        forex=forex,
    )


def scan_technical_signals(timeframe: str = DEFAULT_TIMEFRAME) -> TechnicalScanResult:
    if timeframe not in VALID_TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {VALID_TIMEFRAMES}")

    stock_universe = _get_stock_universe(settings.technical_stock_universe_size)
    tasks: list[tuple[str, Optional[str], str]] = [
        (sym, name, "stock") for sym, name in stock_universe
    ] + [(sym, label, "forex") for sym, label in FOREX_PAIRS]

    stocks: list[TechnicalCandidate] = []
    forex: list[TechnicalCandidate] = []

    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate, sym, name, cls, timeframe) for sym, name, cls in tasks]
        for fut in as_completed(futures):
            result = fut.result()
            if result is None:
                continue
            (stocks if result.asset_class == "stock" else forex).append(result)

    stocks.sort(key=lambda c: c.stoch_k, reverse=True)
    forex.sort(key=lambda c: c.stoch_k, reverse=True)

    return TechnicalScanResult(
        timeframe=timeframe,
        scanned_at=datetime.now(timezone.utc).isoformat(),
        universe_stock_count=len(stock_universe),
        universe_forex_count=len(FOREX_PAIRS),
        stocks=stocks,
        forex=forex,
    )
