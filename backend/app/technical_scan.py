"""On-demand technical scan (not scheduled, not persisted): looks across liquid Nasdaq
stocks plus a fixed set of major forex pairs for 5- or 15-minute candles where Stochastic
RSI is overbought/oversold AND the same candle's [low, high] range is touching (or nearly
touching) the EMA50 — a confluence some traders use as a reversal/reaction signal."""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import yfinance as yf

from .candles import get_4h_dataframe
from .config import settings
from .scanner import CRYPTO_SYMBOLS, NASDAQ_EXCHANGES, _fetch_all_quotes, _is_derivative
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


def _get_stock_universe(limit: int, min_volume: int = 300_000) -> list[tuple[str, Optional[str]]]:
    """Liquid Nasdaq stocks, ranked by trading volume — a cheap pre-filter so the
    expensive per-symbol candle fetch below only runs on names worth watching."""
    query = EquityQuery(
        "and",
        [
            EquityQuery("eq", ["region", "us"]),
            EquityQuery("is-in", ["exchange", *NASDAQ_EXCHANGES]),
            EquityQuery("gt", ["dayvolume", min_volume]),
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


AOI_LOOKBACK_4H = 90  # ~3 weeks of 4h candles to search for a repeatedly-tested level
AOI_MIN_BARS = AOI_LOOKBACK_4H
AOI_SWING_ORDER = 2  # bars required on each side to confirm a swing high/low (5-bar fractal)
AOI_MIN_TOUCHES = 2  # a level needs at least this many separate swing touches to count as an AOI
AOI_CLUSTER_TOLERANCE_PCT = 0.01  # swing prices within 1% of each other are treated as the same level
AOI_CONFLUENCE_TOLERANCE_PCT = 0.0015  # how close EMA50 / the current candle must be to the zone to count as "at" it


@dataclass
class PatternCandidate:
    symbol: str
    name: Optional[str]
    direction: str  # "short" | "long"
    price: float
    ema: float
    ema_period: int
    zone_low: float
    zone_high: float
    touch_count: int
    last_touch_time: str
    candle_time: str


@dataclass
class PatternScanResult:
    scanned_at: str
    universe_stock_count: int
    stocks: list[PatternCandidate]


def _find_swing_points(df: pd.DataFrame, order: int = AOI_SWING_ORDER) -> tuple[list[int], list[int]]:
    """Fractal-style local extrema: bar i is a swing high/low if its High/Low is the
    unique max/min within the `order`-bar window on each side of it."""
    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()
    n = len(df)
    swing_highs: list[int] = []
    swing_lows: list[int] = []
    for i in range(order, n - order):
        window_high = highs[i - order : i + order + 1]
        if highs[i] == window_high.max() and (window_high == highs[i]).sum() == 1:
            swing_highs.append(i)
        window_low = lows[i - order : i + order + 1]
        if lows[i] == window_low.min() and (window_low == lows[i]).sum() == 1:
            swing_lows.append(i)
    return swing_highs, swing_lows


def _cluster_levels(
    points: list[tuple[int, float]], tolerance_pct: float, min_touches: int
) -> list[tuple[float, float, list[int]]]:
    """Groups nearby swing prices into levels: sorted-by-price points within
    tolerance_pct of the level's anchor (its lowest price) collapse into the same
    level. Anchored to the low rather than chained neighbor-to-neighbor, so a level
    can't drift arbitrarily wide through a run of touches each a bit further out than
    the last. A level only counts if it was touched at least min_touches separate
    times. Returns (level_low, level_high, bar_indices) per qualifying level."""
    if not points:
        return []
    ordered = sorted(points, key=lambda p: p[1])
    levels: list[tuple[float, float, list[int]]] = []
    current_idx = [ordered[0][0]]
    current_prices = [ordered[0][1]]
    for idx, price in ordered[1:]:
        if price - current_prices[0] <= current_prices[0] * tolerance_pct:
            current_idx.append(idx)
            current_prices.append(price)
        else:
            if len(current_idx) >= min_touches:
                levels.append((min(current_prices), max(current_prices), current_idx))
            current_idx = [idx]
            current_prices = [price]
    if len(current_idx) >= min_touches:
        levels.append((min(current_prices), max(current_prices), current_idx))
    return levels


def _evaluate_pattern_strategy(symbol: str, name: Optional[str]) -> Optional[PatternCandidate]:
    """The user's own strategy: an "area of interest" (AOI) on the 4h chart — a
    support or resistance level price has reacted from at least twice — sitting right
    where EMA50 currently is, with the current 4h candle back at that zone."""
    df4h = get_4h_dataframe(symbol)
    if df4h is None or len(df4h) < AOI_MIN_BARS:
        return None
    window = df4h.iloc[-AOI_LOOKBACK_4H:]
    ema = window["Close"].ewm(span=settings.technical_ema_period, adjust=False).mean()
    last_ema = ema.iloc[-1]
    if pd.isna(last_ema):
        return None

    swing_highs, swing_lows = _find_swing_points(window)
    resistance_levels = _cluster_levels(
        [(i, float(window["High"].iloc[i])) for i in swing_highs], AOI_CLUSTER_TOLERANCE_PCT, AOI_MIN_TOUCHES
    )
    support_levels = _cluster_levels(
        [(i, float(window["Low"].iloc[i])) for i in swing_lows], AOI_CLUSTER_TOLERANCE_PCT, AOI_MIN_TOUCHES
    )

    last_low, last_high, last_close = (
        float(window["Low"].iloc[-1]), float(window["High"].iloc[-1]), float(window["Close"].iloc[-1])
    )
    tolerance = last_close * AOI_CONFLUENCE_TOLERANCE_PCT

    def zone_matches(zone_low: float, zone_high: float) -> bool:
        ema_at_zone = zone_low - tolerance <= last_ema <= zone_high + tolerance
        candle_at_zone = last_low - tolerance <= zone_high and last_high + tolerance >= zone_low
        return ema_at_zone and candle_at_zone

    best: Optional[tuple[str, float, float, list[int]]] = None
    for zone_low, zone_high, idxs in resistance_levels:
        if zone_matches(zone_low, zone_high) and (best is None or len(idxs) > len(best[3])):
            best = ("short", zone_low, zone_high, idxs)
    for zone_low, zone_high, idxs in support_levels:
        if zone_matches(zone_low, zone_high) and (best is None or len(idxs) > len(best[3])):
            best = ("long", zone_low, zone_high, idxs)

    if best is None:
        return None
    direction, zone_low, zone_high, idxs = best

    return PatternCandidate(
        symbol=symbol,
        name=name,
        direction=direction,
        price=round(last_close, 2),
        ema=round(float(last_ema), 2),
        ema_period=settings.technical_ema_period,
        zone_low=round(zone_low, 2),
        zone_high=round(zone_high, 2),
        touch_count=len(idxs),
        last_touch_time=window.index[max(idxs)].to_pydatetime().astimezone(timezone.utc).isoformat(),
        candle_time=window.index[-1].to_pydatetime().astimezone(timezone.utc).isoformat(),
    )


def scan_pattern_strategy() -> PatternScanResult:
    """On-demand scan (not scheduled, not persisted) for liquid Nasdaq stocks matching
    the user's own strategy: a multi-touch support/resistance area (AOI) on the 4h
    chart, confluent with EMA50, that the current 4h candle is sitting back at."""
    stock_universe = _get_stock_universe(settings.technical_stock_universe_size)

    stocks: list[PatternCandidate] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate_pattern_strategy, sym, name) for sym, name in stock_universe]
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                stocks.append(result)

    stocks.sort(key=lambda c: c.touch_count, reverse=True)

    return PatternScanResult(
        scanned_at=datetime.now(timezone.utc).isoformat(),
        universe_stock_count=len(stock_universe),
        stocks=stocks,
    )


# 4h candles, built from 1h bars — this much 1h history leaves EMA50 well settled even
# for stocks, which only get 2 four-hour candles per session.
TRIPLE_TOUCH_HISTORY_PERIOD = "6mo"
# Every distance below is measured in "candle units" — the average 4h high-low range
# over the last TRIPLE_TOUCH_UNIT_BARS bars — so one rule fits a $400 stock, a $3 stock
# and a forex pair moving a few pips an hour alike.
TRIPLE_TOUCH_UNIT_BARS = 50
TRIPLE_TOUCH_MIN_REJECTIONS = 3  # completed "came to the level and fell back" cycles
# First touch → last touch must fit in this many 4h candles ("not too drawn out").
# Counted in candles, not days: 12 is two days on forex/crypto (6 candles a day) and
# about six trading days on stocks (2 a session) — the spread the user trades on each.
TRIPLE_TOUCH_MAX_SPAN = 12
TRIPLE_TOUCH_MAX_AGE = 4  # the last touch must be within this many candles of now to still be a live setup
TRIPLE_TOUCH_ZONE_UNITS = 0.75  # touches within this far of the level's extreme count as "the same place"
TRIPLE_TOUCH_PULLBACK_UNITS = 2.0  # between touches price must move at least this far away from the level
# ...and get there with a breakout-like push: at least one candle after the touch must
# move away from the level by this much, close vs previous close (so a gap counts but a
# gap that's then sold off doesn't). An average 4h candle nets roughly half a unit, so
# this asks for about twice a normal candle's move.
TRIPLE_TOUCH_PUSH_UNITS = 0.75
TRIPLE_TOUCH_MAX_DISTANCE_UNITS = 6.0  # price already ran further than this from the level → setup has played out
# Trend filter: EMA50 now vs this many candles ago decides whether it's falling or rising.
TRIPLE_TOUCH_EMA_SLOPE_BARS = 10
# Stocks only: a stock whose 4h candles barely move (e.g. pinned by a pending buyout)
# "touches" any level on tiny wiggles. No price floor — penny stocks are in on purpose.
TRIPLE_TOUCH_MIN_STOCK_UNIT_PCT = 0.004
# The stock universe is a couple of thousand symbols, one Yahoo request each. Fired as
# fast as the workers go (~20/s) it gets the whole server rate-limited by Yahoo for a
# while, so the stock scan is paced; forex/crypto are a few dozen symbols and aren't.
TRIPLE_TOUCH_STOCK_REQUESTS_PER_SECOND = 5.0

TRIPLE_TOUCH_MARKETS = ("stocks", "forex", "crypto")
# Wider than FOREX_PAIRS — this pattern is common on the crosses too, and the user asked
# for as many currencies as possible.
TRIPLE_TOUCH_EXTRA_FOREX = [
    ("EURAUD=X", "EUR/AUD"),
    ("EURCAD=X", "EUR/CAD"),
    ("EURNZD=X", "EUR/NZD"),
    ("GBPAUD=X", "GBP/AUD"),
    ("GBPCAD=X", "GBP/CAD"),
    ("GBPCHF=X", "GBP/CHF"),
    ("GBPNZD=X", "GBP/NZD"),
    ("AUDCAD=X", "AUD/CAD"),
    ("AUDCHF=X", "AUD/CHF"),
    ("AUDNZD=X", "AUD/NZD"),
    ("CADJPY=X", "CAD/JPY"),
    ("CHFJPY=X", "CHF/JPY"),
    ("NZDJPY=X", "NZD/JPY"),
    ("NZDCAD=X", "NZD/CAD"),
    ("CADCHF=X", "CAD/CHF"),
]


@dataclass
class TripleTouchCandidate:
    symbol: str
    name: Optional[str]
    direction: str  # "short" = rejected from resistance | "long" = bounced from support
    price: float
    level: float  # where the touches cluster: median of the touch candles' highs (resistance) / lows (support)
    zone_edge: float  # the other side of the zone — any touch reached at least this far
    touch_count: int
    at_level: bool  # price is back at the level right now (another test in progress)
    distance_pct: float  # current close's distance from the level, % of price
    stoch_k: float  # Stoch RSI %K on the current 4h candle
    touch_times: list[str]  # the extreme candle of each touch, oldest first
    candle_time: str


@dataclass
class TripleTouchScanResult:
    market: str
    scanned_at: str
    universe_count: int
    results: list[TripleTouchCandidate]


def _find_repeated_rejections(
    tops: pd.Series, bottoms: pd.Series, closes: pd.Series, unit: float
) -> Optional[tuple[float, float, list[int], bool]]:
    """Written for the resistance case: price climbs into a zone just under a level and
    falls away from it, again and again. The support case reuses it on negated prices.

    A "touch" is a run of consecutive candles whose top reaches the zone; it only ends
    once price has dropped at least TRIPLE_TOUCH_PULLBACK_UNITS away from the level, with
    at least one candle along the way falling TRIPLE_TOUCH_PUSH_UNITS or more, so a few
    candles hovering at the level count once, not several times, and a slow drift away
    isn't a rejection. Both are counted from the touch's highest candle. The zone hangs
    from the highest top since the first touch, which also means nothing has broken
    above it since — but the level reported is the median of the touch tops, i.e. where
    price actually kept turning, so one wick poking past the others doesn't drag the
    level line away from them. Tries each recent bar as the first touch and keeps the
    earliest one that yields enough completed rejections inside the span/age limits.

    The first touch must be price rising into the level (its top above the candle
    before it). Price sliding down into the zone from above is falling *through* the
    level, not being rejected by it. Later touches always come up from a pullback.

    Returns (level, zone_edge, touch bar positions, at_level) or None."""
    top = tops.to_numpy()
    bottom = bottoms.to_numpy()
    close = closes.to_numpy()
    n = len(top)
    earliest = max(1, n - TRIPLE_TOUCH_MAX_SPAN - TRIPLE_TOUCH_MAX_AGE - 1)

    for start in range(earliest, n):
        level = top[start:].max()
        zone_edge = level - TRIPLE_TOUCH_ZONE_UNITS * unit
        if top[start] < zone_edge:
            continue
        pullback_line = level - TRIPLE_TOUCH_PULLBACK_UNITS * unit

        touches: list[int] = []
        armed = True  # ready to register a new touch (price has been rejected since the last one)
        pulled_back = pushed = False
        for i in range(start, n):
            touched_now = top[i] >= zone_edge
            if touched_now:
                if armed:
                    touches.append(i)
                    armed = False
                    pulled_back = pushed = False
                elif top[i] > top[touches[-1]]:
                    touches[-1] = i
                    pulled_back = pushed = False
            if not armed:
                # A single candle can both tag the level and fall away from it, but a wick
                # alone doesn't say which came first — only trusted when it *closes* past
                # the pullback line (a real rejection candle).
                pulled_back = pulled_back or (
                    bottom[i] <= pullback_line and (not touched_now or close[i] <= pullback_line)
                )
                pushed = pushed or close[i - 1] - close[i] >= TRIPLE_TOUCH_PUSH_UNITS * unit
                armed = pulled_back and pushed

        if top[touches[0]] <= top[touches[0] - 1]:
            continue
        rejections = len(touches) if armed else len(touches) - 1
        if rejections < TRIPLE_TOUCH_MIN_REJECTIONS:
            continue
        if touches[-1] - touches[0] > TRIPLE_TOUCH_MAX_SPAN:
            continue
        if n - 1 - touches[-1] > TRIPLE_TOUCH_MAX_AGE:
            continue
        touch_level = float(pd.Series(top[touches]).median())
        # Pulled back without the push isn't a rejection, but price isn't at the level
        # either — unless it has since drifted back into the zone.
        at_level = not armed and (not pulled_back or top[n - 1] >= zone_edge)
        return touch_level, float(zone_edge), touches, at_level

    return None


def _detect_triple_touch(df: pd.DataFrame) -> Optional[tuple[str, float, float, list[pd.Timestamp], bool, float]]:
    """4h candles that hit the same price area at least three times within a short
    stretch and got pushed back each time — but only in the direction of the trend, like
    the setup this models: rejections from resistance while price is below a falling
    EMA50 (short), or bounces off support while it's above a rising EMA50 (long). A
    support "bounce" in a downtrend is just a pause on the way down. Stoch RSI must
    also leave the trade room to run: no short while it's already oversold, no long
    while it's already overbought.

    Returns (direction, level, zone_edge, touch candle times, at_level, stoch_k) or None."""
    # +1 for the current candle, +1 so the earliest possible first touch still has a
    # candle before it to compare against.
    window_len = TRIPLE_TOUCH_MAX_SPAN + TRIPLE_TOUCH_MAX_AGE + 2
    if len(df) < max(TRIPLE_TOUCH_UNIT_BARS, window_len, settings.technical_ema_period + TRIPLE_TOUCH_EMA_SLOPE_BARS):
        return None

    unit = float((df["High"] - df["Low"]).iloc[-TRIPLE_TOUCH_UNIT_BARS:].mean())
    if not unit > 0:
        return None

    ema = df["Close"].ewm(span=settings.technical_ema_period, adjust=False).mean()
    last_close = float(df["Close"].iloc[-1])
    last_ema = float(ema.iloc[-1])
    earlier_ema = float(ema.iloc[-1 - TRIPLE_TOUCH_EMA_SLOPE_BARS])
    downtrend = last_close < last_ema and last_ema < earlier_ema
    uptrend = last_close > last_ema and last_ema > earlier_ema
    if not (downtrend or uptrend):
        return None

    window = df.iloc[-window_len:]
    found: list[tuple[str, float, float, list[int], bool]] = []
    if downtrend:
        resistance = _find_repeated_rejections(window["High"], window["Low"], window["Close"], unit)
        if resistance:
            found.append(("short", *resistance))
    if uptrend:
        support = _find_repeated_rejections(-window["Low"], -window["High"], -window["Close"], unit)
        if support:
            level, zone_edge, touches, at_level = support
            found.append(("long", -level, -zone_edge, touches, at_level))
    if not found:
        return None

    direction, level, zone_edge, touches, at_level = found[0]  # the trends are exclusive, so at most one side
    if abs(last_close - level) > TRIPLE_TOUCH_MAX_DISTANCE_UNITS * unit:
        return None

    stoch_k = float(_stoch_rsi(df["Close"])[0].iloc[-1])
    if pd.isna(stoch_k):
        return None
    if direction == "short" and stoch_k <= settings.technical_stochrsi_oversold:
        return None
    if direction == "long" and stoch_k >= settings.technical_stochrsi_overbought:
        return None
    return direction, level, zone_edge, [window.index[p] for p in touches], at_level, stoch_k


class _RateLimiter:
    """Spaces wait() returns at least 1/per_second apart, across all threads."""

    def __init__(self, per_second: float):
        self._interval = 1 / per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._interval
        time.sleep(slot - now)


_stock_fetch_limiter = _RateLimiter(TRIPLE_TOUCH_STOCK_REQUESTS_PER_SECOND)


def _round_sig(value: float) -> float:
    # 6 significant digits covers a $500 stock, a 1.37245 forex rate and a 0.0000123 coin.
    return float(f"{value:.6g}")


def _evaluate_triple_touch(symbol: str, name: Optional[str], market: str) -> Optional[TripleTouchCandidate]:
    if market == "stocks":
        _stock_fetch_limiter.wait()
    df = get_4h_dataframe(symbol, period=TRIPLE_TOUCH_HISTORY_PERIOD)
    if df is None or df.empty:
        return None
    last_close = float(df["Close"].iloc[-1])
    if market == "stocks":
        # Median, not mean — one gap candle (e.g. the buyout announcement) would
        # otherwise mask weeks of flat-lined bars.
        typical_range = (df["High"] - df["Low"]).iloc[-TRIPLE_TOUCH_UNIT_BARS:].median()
        if typical_range < last_close * TRIPLE_TOUCH_MIN_STOCK_UNIT_PCT:
            return None

    detected = _detect_triple_touch(df)
    if detected is None:
        return None
    direction, level, zone_edge, touch_times, at_level, stoch_k = detected

    def iso(ts: pd.Timestamp) -> str:
        return ts.to_pydatetime().astimezone(timezone.utc).isoformat()

    return TripleTouchCandidate(
        symbol=symbol,
        name=name,
        direction=direction,
        price=_round_sig(last_close),
        level=_round_sig(level),
        zone_edge=_round_sig(zone_edge),
        touch_count=len(touch_times),
        at_level=at_level,
        distance_pct=round(abs(last_close - level) / last_close * 100, 2),
        stoch_k=round(stoch_k, 1),
        touch_times=[iso(ts) for ts in touch_times],
        candle_time=iso(df.index[-1]),
    )


def scan_triple_touch_4h(market: str) -> TripleTouchScanResult:
    """On-demand scan (not scheduled, not persisted) for the triple-rejection pattern on
    4h candles, over practically every traded Nasdaq stock (small caps and penny stocks
    included), major + cross forex pairs, or the curated crypto list, depending on
    `market`."""
    if market not in TRIPLE_TOUCH_MARKETS:
        raise ValueError(f"market must be one of {TRIPLE_TOUCH_MARKETS}")

    if market == "stocks":
        universe = _get_stock_universe(
            settings.triple_touch_stock_universe_size, min_volume=settings.triple_touch_min_stock_volume
        )
    elif market == "forex":
        universe = FOREX_PAIRS + TRIPLE_TOUCH_EXTRA_FOREX
    else:
        universe = [(sym, sym.removesuffix("-USD")) for sym in CRYPTO_SYMBOLS]

    results: list[TripleTouchCandidate] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate_triple_touch, sym, name, market) for sym, name in universe]
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                results.append(result)

    results.sort(key=lambda c: c.touch_times[-1], reverse=True)

    return TripleTouchScanResult(
        market=market,
        scanned_at=datetime.now(timezone.utc).isoformat(),
        universe_count=len(universe),
        results=results,
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


# "Shilo 2": Stoch RSI at an extreme on the daily chart AND on the 4h chart at the same
# time, in the same direction — a stretched move both the bigger and the smaller
# timeframe agree on. Overbought on one and oversold on the other doesn't count.
STOCH_EXTREME_DAILY_PERIOD = "1y"  # ~250 daily bars, plenty for the RSI to settle
STOCH_EXTREME_4H_PERIOD = "3mo"  # of 1h bars → ~125 four-hour candles


@dataclass
class StochExtremeCandidate:
    symbol: str
    name: Optional[str]
    signal: str  # "overbought" | "oversold" — the same on both timeframes
    price: float
    daily_k: float
    daily_d: float
    h4_k: float
    h4_d: float
    candle_time: str  # the current 4h candle


@dataclass
class StochExtremeScanResult:
    scanned_at: str
    universe_stock_count: int
    stocks: list[StochExtremeCandidate]


def _stoch_extreme(close: pd.Series) -> Optional[tuple[str, float, float]]:
    """(signal, %K, %D) when the latest candle's Stoch RSI %K is overbought/oversold —
    the same thresholds as the intraday technical scan — else None."""
    if len(close) < MIN_BARS:
        return None
    k, d = _stoch_rsi(close)
    last_k, last_d = k.iloc[-1], d.iloc[-1]
    if pd.isna(last_k) or pd.isna(last_d):
        return None
    if last_k >= settings.technical_stochrsi_overbought:
        return "overbought", float(last_k), float(last_d)
    if last_k <= settings.technical_stochrsi_oversold:
        return "oversold", float(last_k), float(last_d)
    return None


def _evaluate_stoch_extreme(symbol: str, name: Optional[str]) -> Optional[StochExtremeCandidate]:
    # Daily first: most stocks aren't stretched on the daily chart, and those are dropped
    # before spending a second (rate-limited) request on their 1h bars.
    _stock_fetch_limiter.wait()
    try:
        daily = yf.Ticker(symbol).history(interval="1d", period=STOCH_EXTREME_DAILY_PERIOD)
    except Exception:
        logger.warning("Stoch extreme scan: daily history fetch failed for %s", symbol)
        return None
    if daily is None or daily.empty:
        return None
    on_daily = _stoch_extreme(daily["Close"].dropna())
    if on_daily is None:
        return None

    _stock_fetch_limiter.wait()
    df4h = get_4h_dataframe(symbol, period=STOCH_EXTREME_4H_PERIOD)
    if df4h is None or df4h.empty:
        return None
    last_close = float(df4h["Close"].iloc[-1])
    # Same filter as the triple-touch scan: a stock pinned in place (e.g. by a pending
    # buyout) swings Stoch RSI from end to end on tiny wiggles.
    typical_range = (df4h["High"] - df4h["Low"]).iloc[-TRIPLE_TOUCH_UNIT_BARS:].median()
    if typical_range < last_close * TRIPLE_TOUCH_MIN_STOCK_UNIT_PCT:
        return None
    on_4h = _stoch_extreme(df4h["Close"])
    if on_4h is None or on_4h[0] != on_daily[0]:
        return None

    signal, daily_k, daily_d = on_daily
    _, h4_k, h4_d = on_4h
    return StochExtremeCandidate(
        symbol=symbol,
        name=name,
        signal=signal,
        price=_round_sig(last_close),
        daily_k=round(daily_k, 1),
        daily_d=round(daily_d, 1),
        h4_k=round(h4_k, 1),
        h4_d=round(h4_d, 1),
        candle_time=df4h.index[-1].to_pydatetime().astimezone(timezone.utc).isoformat(),
    )


def scan_stoch_extreme_daily_4h() -> StochExtremeScanResult:
    """On-demand scan (not scheduled, not persisted) for Nasdaq stocks whose Stoch RSI is
    overbought on both the daily and the 4h chart, or oversold on both."""
    stock_universe = _get_stock_universe(
        settings.stoch_extreme_stock_universe_size, min_volume=settings.stoch_extreme_min_stock_volume
    )

    stocks: list[StochExtremeCandidate] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate_stoch_extreme, sym, name) for sym, name in stock_universe]
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                stocks.append(result)

    # Overbought first, then oversold; within each, the most stretched (both %K values
    # furthest into the extreme) on top.
    stocks.sort(key=lambda c: (c.signal != "overbought", -abs(c.daily_k + c.h4_k - 100)))

    return StochExtremeScanResult(
        scanned_at=datetime.now(timezone.utc).isoformat(),
        universe_stock_count=len(stock_universe),
        stocks=stocks,
    )
