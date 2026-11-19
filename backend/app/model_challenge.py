"""The app's models page (the user's ask, 10/11/26): every time a new model comes out it picks
an asset, a timeframe and a strategy, and the page shows whether that makes money month by
month — "profitable at the end of the month, even by 1%". Day trading only (the user,
11/11/26): in and out the same day.

A model's pick is an entry in ENTRIES, kept in code: the rule is fixed on the day it was
picked and never tuned afterwards. The months are worked out again from Yahoo's daily candles
on every load, so the months after the pick are a real forward test. A new model = a new
entry on top; the old ones stay, to compare.

The one rule so far, the opening breakout, needs daily candles only: its orders sit a set
distance above and below the open, the stop is the other order and the exit is the close, so
a day's open, high, low and close tell its result whatever the order inside the day."""
import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

PASS_AVG_PCT = 1.0  # the user's bar: 1% a month on average, after costs
TEST_FROM = "2022-01"  # the months shown; a pick is chosen on the years before only
FETCH_FROM = "2021-06-01"
NEW_YORK = ZoneInfo("America/New_York")
SESSION_OVER = (16, 15)  # New York; before it today's candle is still moving
CACHE_TTL_SECONDS = 3600
_cache: Optional[tuple[float, list[dict]]] = None
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class Entry:
    id: str
    model: str
    picked_at: date
    asset: str
    timeframe: str
    strategy: str
    rules: tuple[str, ...]
    note: str  # what the model found, in a line or two
    symbol: str
    k: float  # the orders' distance from the open, in yesterday's ranges
    share: float  # of the account in a trade
    cost: float  # a side, of what's traded


# Newest first.
ENTRIES = (
    Entry(
        id="fable-5-2",
        model="Fable 5.2",
        picked_at=date(2026, 11, 11),
        asset="SOXL (שבבים, ממונף פי 3)",
        timeframe="מסחר יומי, עסקה אחת ביום",
        strategy="פריצת פתיחה",
        rules=(
            "בפתיחת המסחר בניו יורק שמים שתי פקודות: קנייה מעל מחיר הפתיחה ומכירה בשורט מתחתיו",
            "המרחק של כל פקודה מהפתיחה: 0.3 מהטווח של אתמול (הגבוה פחות הנמוך)",
            "הפקודה הראשונה שנתפסת היא העסקה, והשנייה נשארת בתור הסטופ שלה",
            "יוצאים בסגירת המסחר, לא נשארים ללילה",
            "בכל עסקה שליש מהכסף בחשבון",
        ),
        note=(
            "נבדקו 560 שילובים של נכס ואסטרטגיה תוך־יומית על השנים עד 2021, ורק על SOXL זה עבד. "
            "הסיבה המוכרת: קרן ממונפת חייבת לקנות ביום עולה ולמכור ביום יורד, וזה דוחף את המחיר הלאה. "
            "נבדק גם מול נרות של שעה ושל 5 דקות, אותה תוצאה. "
            "החישוב מניח עלות של 0.05% לכל צד; ב-0.2% לצד הרווח נעלם. "
            "זה מסוכן: ב-2020 האסטרטגיה הפסידה כשליש מהחשבון, ואין רווח בכל חודש."
        ),
        symbol="SOXL",
        k=0.3,
        share=1 / 3,
        cost=0.0005,
    ),
)


def whole_days(df: pd.DataFrame, now: Optional[datetime] = None) -> pd.DataFrame:
    """Without today's candle while New York's session is still running."""
    now = (now or datetime.now(NEW_YORK)).astimezone(NEW_YORK)
    if len(df) and df.index[-1].date() == now.date() and (now.hour, now.minute) < SESSION_OVER:
        return df.iloc[:-1]
    return df


def _candles(symbol: str) -> Optional[pd.DataFrame]:
    """Daily open, high, low and close, whole days only."""
    try:
        df = yf.Ticker(symbol).history(interval="1d", start=FETCH_FROM)
    except Exception:
        logger.warning("Model challenge: candle fetch failed for %s", symbol)
        return None
    if df is None or df.empty:
        return None
    df = df[["Open", "High", "Low", "Close"]].dropna()
    df.index = df.index.tz_localize(None).normalize()
    return whole_days(df)


def breakout_returns(df: pd.DataFrame, k: float, cost: float) -> pd.Series:
    """Each day's return on the money in the trade, after costs: 0 with no trade. A day that
    reached both orders is the full loss between them, whichever came first."""
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    distance = k * (h - l).shift(1)
    buy, sell = o + distance, o - distance
    bought, sold = h >= buy, l <= sell
    out = pd.Series(0.0, index=df.index)
    out[bought] = c / buy - 1
    out[sold] = sell / c - 1
    out[bought & sold] = -(buy - sell) / buy
    out[bought | sold] -= 2 * cost
    return out


def _monthly(returns: pd.Series) -> pd.Series:
    return (1 + returns).groupby(returns.index.to_period("M")).prod() - 1


def _drawdown(returns: pd.Series) -> float:
    equity = (1 + returns).cumprod()
    return float((equity / equity.cummax() - 1).min())


def _pct(x: float) -> float:
    return round(float(x) * 100, 2)


def _summary(returns: pd.Series) -> dict:
    """Over whole months only."""
    months = _monthly(returns)
    return {
        "months": len(months),
        "avg": _pct(months.mean()),
        "positive": int((months > 0).sum()),
        "worst": _pct(months.min()),
        "drawdown": _pct(_drawdown(returns)),
        "total": _pct((1 + returns).prod() - 1),
    }


def evaluate(entry: Entry, df: pd.DataFrame, today: Optional[date] = None) -> dict:
    returns = (breakout_returns(df, entry.k, entry.cost) * entry.share)[TEST_FROM:]
    hold = (df["Close"].pct_change().fillna(0.0) * entry.share)[TEST_FROM:]  # the same money, just held

    this_month = pd.Period(today or date.today(), "M")
    whole = returns[returns.index.to_period("M") < this_month]
    months = _monthly(returns)
    picked = pd.Period(entry.picked_at, "M")
    since = returns[returns.index >= pd.Timestamp(entry.picked_at)]
    test = _summary(whole)
    last = df.iloc[-1]
    return {
        "id": entry.id,
        "model": entry.model,
        "picked_at": entry.picked_at.isoformat(),
        "asset": entry.asset,
        "timeframe": entry.timeframe,
        "strategy": entry.strategy,
        "rules": list(entry.rules),
        "note": entry.note,
        "passed": test["avg"] >= PASS_AVG_PCT,
        "test": test,
        "hold": _summary(hold[: whole.index[-1]]),
        "last12_avg": _pct(_monthly(whole).iloc[-12:].mean()),
        "trades": {"count": int((whole != 0).sum()), "wins": int((whole > 0).sum())},
        "since_pick": {"days": len(since), "total": _pct((1 + since).prod() - 1)},
        # the next session's orders: this far above and below its open
        "next_distance": round(float(entry.k * (last["High"] - last["Low"])), 2),
        "months": [
            {"month": str(m), "pct": _pct(v), "live": m >= picked, "running": m == this_month}
            for m, v in months.items()
        ],
    }


def results() -> list[dict]:
    """Every model's pick with its months, newest first; a pick whose candles didn't load is
    left out."""
    global _cache
    now = time.monotonic()
    with _cache_lock:
        if _cache and now - _cache[0] < CACHE_TTL_SECONDS:
            return _cache[1]
    out = []
    for entry in ENTRIES:
        df = _candles(entry.symbol)
        if df is None or len(df) < 2:
            continue
        try:
            out.append(evaluate(entry, df))
        except Exception:
            logger.exception("Model challenge: couldn't work out %s", entry.id)
    if len(out) == len(ENTRIES):
        with _cache_lock:
            _cache = (now, out)
    return out
