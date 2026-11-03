"""The "שונות" bot (the user's ask, 2/11/26): five day-trading strategies on US stocks, on
5-minute candles during the session, each alert tagged with its strategy. It sends from
the old penny stocks bot (@Expostocksss_bot, TELEGRAM_PENNY_BOT_TOKEN — the env name
kept), idle since 29/10/26.

- ORB: the first 15 minutes' high and low; the day's first candle closing over the high,
  green → long (under the low, red → short). The stop at the range's middle.
- Mean reversion: price stretched far under its mean (SMA20 less STRETCH_ATR ATRs), then a
  clear turn — the entry candle (trade_plan.is_entry_candle) right off the move's low →
  long, toward the mean, which has to lie past the target. The stop under the low.
- Trend following: EMA20 over EMA50 for a while and the EMA50 rising; a small pullback
  down to the EMA20, then the entry candle → long. The stop under the pullback.
- Liquidity sweep: a candle wicks over the session's clear high so far and closes back
  under it, red → short. The stop just over its wick.
- VWAP pullback: price held over the day's VWAP, which rises; back down to it, then the
  entry candle → long. The stop under the pullback (or the VWAP).

Each mirrors to the other side. The stocks: ~100 big, liquid Nasdaq names, and the day's
movers — the most up and the most down (the user picked both); a stock only while it's
"in play", trading at least MIN_RVOL times its usual volume by this time of day. Every
trade is 1:2, as on the user's pictures, and no entry comes in the session's last hour,
as on the Nasdaq bot.

On 60 days of the big names, without the volume rule, the five came to ~290 signals a
day, every one about even (win 33-35% at 1:2, -0.02R to +0.05R a trade, before costs).
The volume rule leaves ~17 a day, no better or worse.
A strategy alerts once a day per stock and side. Checked right after each 5m candle
closes (scheduler.py); the trade is watched until its target or stop (trade_tracker.py).
`python -m app.misc_strategies N` sends the last N signals of each strategy from the past
60 days, marked as examples."""
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from sqlalchemy.orm import Session

from . import alert_chart, telegram, trade_tracker
from .candles import Candle, _to_candles
from .config import settings
from .models import SentAlert
from .scanner import scan_nasdaq
from .trade_plan import TradePlan, is_entry_candle, plan

logger = logging.getLogger(__name__)

BOT = "penny"
TIMEFRAME = "5m"
BAR_SECONDS = 300
LIVE_PERIOD = "5d"  # enough candles for the EMA50 to settle
HISTORY_PERIOD = "60d"  # Yahoo's limit for 5m bars
NY = ZoneInfo("America/New_York")
SESSION_OPEN = (9, 30)
# No entry from this time (New York) to the close — the hour before it.
LAST_HOUR = (15, 0)
REWARD_RISK = 2
# How far past the level or the wicks the stop goes, in ATRs.
STOP_BUFFER = 0.1
ATR_PERIOD = 14

# Big, liquid Nasdaq names (the Nasdaq 100's, mostly); a name Yahoo has no candles for is
# skipped.
LARGE_CAPS = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "COST", "NFLX",
    "AMD", "PEP", "ADBE", "CSCO", "TMUS", "INTC", "QCOM", "TXN", "AMGN", "INTU",
    "AMAT", "ISRG", "CMCSA", "HON", "BKNG", "VRTX", "ADP", "SBUX", "GILD", "MU",
    "ADI", "LRCX", "PANW", "MDLZ", "REGN", "KLAC", "SNPS", "CDNS", "MELI", "PYPL",
    "ASML", "MAR", "CRWD", "ABNB", "ORLY", "CTAS", "MRVL", "FTNT", "NXPI", "CSX",
    "WDAY", "PCAR", "ROP", "ADSK", "MNST", "CPRT", "DASH", "PDD", "ROST", "AEP",
    "CHTR", "PAYX", "KDP", "FAST", "ODFL", "KHC", "EXC", "AZN", "DDOG", "TTD",
    "BKR", "CTSH", "VRSK", "XEL", "GEHC", "LULU", "IDXX", "CCEP", "FANG",
    "TEAM", "ON", "CSGP", "ZS", "DXCM", "BIIB", "CDW", "GFS", "MDB", "WBD", "ILMN",
    "ARM", "SMCI", "PLTR", "APP", "MSTR", "AXON", "COIN", "HOOD", "SHOP", "LIN",
)
# In play: the session's volume so far at least this many times the average of the last
# RVOL_DAYS sessions' by the same candle (at least RVOL_MIN_DAYS of them).
MIN_RVOL = 2.0
RVOL_DAYS = 10
RVOL_MIN_DAYS = 2
# The day's movers: the most up and the most down on the Nasdaq (scanner.scan_nasdaq),
# from $1 and with real volume, refreshed this often through the session.
MOVERS_PER_SIDE = 15
MOVERS_MIN_PERCENT = 8.0
MOVERS_MIN_VOLUME = 500_000
MOVERS_MIN_PRICE = 1.0
MOVERS_TTL_SECONDS = 1800

# ORB: the opening range's candles (15 minutes).
ORB_BARS = 3
# Mean reversion: the mean, how far under it the move's low has to reach (in ATRs), and
# the candles the low has to be the lowest of (an hour) — within the last LOW_RECENT.
MEAN_PERIOD = 20
STRETCH_ATR = 3.0
DROP_BARS = 12
LOW_RECENT = 4
# Trend: the EMAs; the fast one over the slow one for TREND_BARS candles (two hours), the
# slow one higher than then; the pullback looked for in the last PULLBACK_BARS.
FAST_EMA = 20
SLOW_EMA = 50
TREND_BARS = 24
PULLBACK_BARS = 5
# Sweep: the high has to be at least this many candles old, and the session this old.
SWEEP_MIN_AGE = 3
SWEEP_MIN_SESSION_BARS = 6
# VWAP: the session at least an hour old; this share of its last VWAP_TREND_BARS candles
# closed over the VWAP, which is higher than VWAP_RISE_BARS ago; a touch within the last
# VWAP_TOUCH_BARS reaches VWAP_REACH ATRs of it.
VWAP_MIN_SESSION_BARS = 12
VWAP_TREND_BARS = 24
VWAP_HELD_SHARE = 0.8
VWAP_RISE_BARS = 6
VWAP_TOUCH_BARS = 3
VWAP_REACH = 0.1

STRATEGIES = {  # id → the caption's tag
    "orb": "ORB",
    "mean": "חזרה לממוצע",
    "trend": "מסחר עם המגמה",
    "sweep": "איסוף נזילות",
    "vwap": "VWAP",
}
TITLES = {"orb": "ORB", "mean": "Mean reversion", "trend": "Trend following", "sweep": "Liquidity sweep", "vwap": "VWAP pullback"}

# A signal is sent only while its candle closed at most this long ago.
MAX_AGE_SECONDS = 2 * BAR_SECONDS
# At most this many alerts a run, the freshest first — a flood helps no one.
MAX_PER_RUN = 15
SEND_INTERVAL_SECONDS = 1.1
# The picture starts at the session's open, with at least this many candles.
PICTURE_MIN_BARS = 30


@dataclass
class Series:
    """One stock's candles with what the strategies read off them, all aligned."""
    candles: list[Candle]
    atr: list[float]
    mean: list[float]  # SMA20
    fast: list[float]  # EMA20
    slow: list[float]  # EMA50
    vwap: list[float]  # the session's, from its open
    day_start: list[int]  # the index of each candle's session's first candle
    rvol: list[float]  # the session's volume so far against the usual by then; NaN = unknown


@dataclass
class Signal:
    strategy: str  # a STRATEGIES id
    symbol: str
    direction: str  # "up" = long | "down" = short
    i: int  # the signal candle's index; in on its close
    plan: TradePlan
    level: Optional[float] = None  # the swept high/low, or the range's edge
    level_from: Optional[int] = None  # the candle it starts at
    zone: Optional[tuple[float, float]] = None  # ORB: the opening range
    session_from: int = 0  # the session's first candle
    candles: list[Candle] = field(default_factory=list, repr=False)  # up to the signal candle
    line: list[float] = field(default_factory=list, repr=False)  # the indicator drawn
    after: list[Candle] = field(default_factory=list, repr=False)  # an example's: the rest of its day

    @property
    def time(self) -> int:
        return self.candles[self.i].time

    @property
    def day(self) -> str:
        return datetime.fromtimestamp(self.time, NY).strftime("%Y-%m-%d")

    @property
    def key(self) -> str:
        # Once a day per stock, strategy and side.
        return f"misc:{self.strategy}:{self.symbol}:{self.direction}:{self.day}"


_run_lock = threading.Lock()
_movers: tuple[float, list[str]] = (0.0, [])


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _clock(ts: int) -> tuple[int, int]:
    t = datetime.fromtimestamp(ts, NY)
    return t.hour, t.minute


def build_series(candles: Sequence[Candle]) -> Series:
    candles = list(candles)
    close = pd.Series([c.close for c in candles], dtype=float)
    high = pd.Series([c.high for c in candles], dtype=float)
    low = pd.Series([c.low for c in candles], dtype=float)
    true_range = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    day_start, vwap, rvol = [], [], []
    sessions: list[list[float]] = []  # each session's volume so far, by candle
    start, pv, vol = 0, 0.0, 0.0
    for i, c in enumerate(candles):
        if i == 0 or datetime.fromtimestamp(c.time, NY).date() != datetime.fromtimestamp(candles[i - 1].time, NY).date():
            start, pv, vol = i, 0.0, 0.0
            sessions.append([])
        day_start.append(start)
        v = float(c.volume or 0)
        pv += (c.high + c.low + c.close) / 3 * v
        vol += v
        vwap.append(pv / vol if vol else c.close)
        sessions[-1].append(vol)
        k = i - start
        usual = [day[k] for day in sessions[-1 - RVOL_DAYS : -1] if len(day) > k and day[k] > 0]
        rvol.append(vol / (sum(usual) / len(usual)) if len(usual) >= RVOL_MIN_DAYS else float("nan"))
    return Series(
        candles=candles,
        atr=true_range.rolling(ATR_PERIOD, min_periods=1).mean().tolist(),
        mean=close.rolling(MEAN_PERIOD, min_periods=1).mean().tolist(),
        fast=close.ewm(span=FAST_EMA, adjust=False).mean().tolist(),
        slow=close.ewm(span=SLOW_EMA, adjust=False).mean().tolist(),
        vwap=vwap,
        day_start=day_start,
        rvol=rvol,
    )


def _stop(s: Series, lo: int, i: int, direction: str, level: Optional[float] = None) -> float:
    """STOP_BUFFER ATRs past the candles lo..i's wicks (or level, if further)."""
    run = s.candles[lo : i + 1]
    if direction == "down":
        return max([c.high for c in run] + ([level] if level is not None else [])) + STOP_BUFFER * s.atr[i]
    return min([c.low for c in run] + ([level] if level is not None else [])) - STOP_BUFFER * s.atr[i]


def _no_earlier_entry(s: Series, first: int, i: int, direction: str) -> bool:
    """No entry candle between first and i (exclusive) — the signal is the setup's first."""
    return not any(is_entry_candle(s.candles[k - 1], s.candles[k], direction) for k in range(max(first, 1), i))


def orb(s: Series, i: int) -> Optional[Signal]:
    start = s.day_start[i]
    if i - start < ORB_BARS or _clock(s.candles[start].time) != SESSION_OPEN:
        return None
    opening = s.candles[start : start + ORB_BARS]
    high, low = max(c.high for c in opening), min(c.low for c in opening)
    c, before = s.candles[i], s.candles[start + ORB_BARS : i]
    if c.close > high and c.close > c.open and all(b.close <= high for b in before):
        direction, level = "up", high
    elif c.close < low and c.close < c.open and all(b.close >= low for b in before):
        direction, level = "down", low
    else:
        return None
    trade = plan(direction, c.close, (high + low) / 2, REWARD_RISK)
    return trade and Signal("orb", "", direction, i, trade, level=level, level_from=start, zone=(low, high))


def mean_reversion(s: Series, i: int) -> Optional[Signal]:
    if i < max(DROP_BARS, MEAN_PERIOD) or i - s.day_start[i] < ORB_BARS:
        return None
    c, prev = s.candles[i], s.candles[i - 1]
    for direction in ("up", "down"):
        if not is_entry_candle(prev, c, direction):
            continue
        run = range(i - DROP_BARS, i + 1)
        if direction == "up":
            j = min(run, key=lambda k: s.candles[k].low)
            stretched = s.candles[j].low <= s.mean[j] - STRETCH_ATR * s.atr[j]
        else:
            j = max(run, key=lambda k: s.candles[k].high)
            stretched = s.candles[j].high >= s.mean[j] + STRETCH_ATR * s.atr[j]
        if not stretched or i - j >= LOW_RECENT or not _no_earlier_entry(s, j + 1, i, direction):
            continue
        trade = plan(direction, c.close, _stop(s, j, i, direction), REWARD_RISK)
        if trade and (s.mean[i] >= trade.target if direction == "up" else s.mean[i] <= trade.target):
            return Signal("mean", "", direction, i, trade)
    return None


def trend_following(s: Series, i: int) -> Optional[Signal]:
    if i < SLOW_EMA + TREND_BARS:
        return None
    c, prev = s.candles[i], s.candles[i - 1]
    trend = range(i - TREND_BARS, i + 1)
    pullback = range(i - PULLBACK_BARS, i)
    if all(s.fast[k] > s.slow[k] for k in trend) and s.slow[i] > s.slow[i - TREND_BARS]:
        direction = "up"
        touched = [k for k in pullback if s.candles[k].low <= s.fast[k]]
        held = all(s.candles[k].close > s.slow[k] for k in range(i - PULLBACK_BARS, i + 1)) and c.close > s.fast[i]
    elif all(s.fast[k] < s.slow[k] for k in trend) and s.slow[i] < s.slow[i - TREND_BARS]:
        direction = "down"
        touched = [k for k in pullback if s.candles[k].high >= s.fast[k]]
        held = all(s.candles[k].close < s.slow[k] for k in range(i - PULLBACK_BARS, i + 1)) and c.close < s.fast[i]
    else:
        return None
    if not touched or not held or not is_entry_candle(prev, c, direction):
        return None
    if not _no_earlier_entry(s, touched[0], i, direction):
        return None
    trade = plan(direction, c.close, _stop(s, touched[0], i, direction), REWARD_RISK)
    return trade and Signal("trend", "", direction, i, trade)


def liquidity_sweep(s: Series, i: int) -> Optional[Signal]:
    start = s.day_start[i]
    if i - start < SWEEP_MIN_SESSION_BARS:
        return None
    c = s.candles[i]
    before = range(start, i)
    top = max(before, key=lambda k: s.candles[k].high)
    bottom = min(before, key=lambda k: s.candles[k].low)
    high, low = s.candles[top].high, s.candles[bottom].low
    if i - top >= SWEEP_MIN_AGE and c.high > high and c.close < high and c.close < c.open:
        direction, j, level = "down", top, high
    elif i - bottom >= SWEEP_MIN_AGE and c.low < low and c.close > low and c.close > c.open:
        direction, j, level = "up", bottom, low
    else:
        return None
    trade = plan(direction, c.close, _stop(s, i, i, direction), REWARD_RISK)
    return trade and Signal("sweep", "", direction, i, trade, level=level, level_from=j)


def vwap_pullback(s: Series, i: int) -> Optional[Signal]:
    start = s.day_start[i]
    if i - start < VWAP_MIN_SESSION_BARS:
        return None
    c, prev = s.candles[i], s.candles[i - 1]
    recent = range(max(start, i - VWAP_TREND_BARS), i)
    above = sum(s.candles[k].close > s.vwap[k] for k in recent) / len(recent)
    touches = range(i - VWAP_TOUCH_BARS, i + 1)
    if above >= VWAP_HELD_SHARE and s.vwap[i] > s.vwap[i - VWAP_RISE_BARS] and c.close > s.vwap[i]:
        direction = "up"
        touched = [k for k in touches if s.candles[k].low <= s.vwap[k] + VWAP_REACH * s.atr[k]]
    elif above <= 1 - VWAP_HELD_SHARE and s.vwap[i] < s.vwap[i - VWAP_RISE_BARS] and c.close < s.vwap[i]:
        direction = "down"
        touched = [k for k in touches if s.candles[k].high >= s.vwap[k] - VWAP_REACH * s.atr[k]]
    else:
        return None
    if not touched or not is_entry_candle(prev, c, direction) or not _no_earlier_entry(s, touched[0], i, direction):
        return None
    trade = plan(direction, c.close, _stop(s, touched[0], i, direction, s.vwap[i]), REWARD_RISK)
    return trade and Signal("vwap", "", direction, i, trade)


DETECTORS: dict[str, Callable[[Series, int], Optional[Signal]]] = {
    "orb": orb, "mean": mean_reversion, "trend": trend_following, "sweep": liquidity_sweep, "vwap": vwap_pullback,
}
LINES = {"mean": ("mean", "SMA 20"), "trend": ("fast", "EMA 20"), "vwap": ("vwap", "VWAP")}


def signals_at(s: Series, i: int, symbol: str) -> list[Signal]:
    """Every strategy's signal on candle i, seeing only the candles up to it — none from
    the pre-market, in the session's last hour or while the stock isn't in play."""
    t = s.candles[i].time
    if not SESSION_OPEN <= _clock(t) or _clock(t + BAR_SECONDS) > LAST_HOUR or not s.rvol[i] >= MIN_RVOL:
        return []
    out = []
    for strategy, detect in DETECTORS.items():
        found = detect(s, i)
        if found:
            found.symbol = symbol
            found.candles = s.candles[: i + 1]
            found.session_from = s.day_start[i]
            if strategy in LINES:
                found.line = getattr(s, LINES[strategy][0])[: i + 1]
            out.append(found)
    return out


def history_signals(symbol: str, candles: Sequence[Candle]) -> list[Signal]:
    """Every signal the live runs would have sent on these candles, oldest first — the
    first of each day per strategy and side."""
    s = build_series(candles)
    seen: set[str] = set()
    out = []
    for i in range(len(s.candles)):
        for found in signals_at(s, i, symbol):
            if found.key not in seen:
                seen.add(found.key)
                found.after = alert_chart.rest_of_day(s.candles, i)
                if found.line:
                    found.line = getattr(s, LINES[found.strategy][0])[: i + 1 + len(found.after)]
                out.append(found)
    return out


def movers() -> list[str]:
    """The day's movers, both ways — cached MOVERS_TTL_SECONDS; none if the screener fails."""
    global _movers
    fetched, symbols = _movers
    if time.time() - fetched < MOVERS_TTL_SECONDS:
        return symbols
    symbols = []
    for side in ("gainers", "losers"):
        try:
            found = scan_nasdaq(side, MOVERS_MIN_PERCENT, MOVERS_MIN_VOLUME).candidates
        except Exception:
            logger.warning("Misc strategies: the %s screener failed", side)
            continue
        symbols += [c.symbol for c in found if (c.price or 0) >= MOVERS_MIN_PRICE][:MOVERS_PER_SIDE]
    _movers = (time.time(), symbols)
    return symbols


def fetch_all(symbols: Sequence[str], period: str = LIVE_PERIOD) -> dict[str, list[Candle]]:
    """Each stock's closed 5m candles of the session — Yahoo includes the one in progress."""
    try:
        df = yf.download(
            list(symbols), period=period, interval=TIMEFRAME, group_by="ticker", threads=True,
            progress=False, prepost=False, auto_adjust=False,
        )
    except Exception:
        logger.warning("Misc strategies: the candles download failed")
        return {}
    if df is None or df.empty:
        return {}
    out = {}
    now = time.time()
    for symbol in symbols:
        try:
            sub = df[symbol] if isinstance(df.columns, pd.MultiIndex) else df
        except KeyError:
            continue
        candles = _to_candles(sub.dropna(how="all"))
        if candles and candles[-1].time + BAR_SECONDS > now:
            candles.pop()
        if candles:
            out[symbol] = candles
    return out


def format_message(s: Signal, example: bool = False) -> str:
    text = telegram.alert_caption(s.symbol, None, s.direction == "up", STRATEGIES[s.strategy])
    if example:
        when = datetime.fromtimestamp(s.time + BAR_SECONDS, ZoneInfo(settings.alert_timezone))
        text += f"\n🕘 דוגמה מההיסטוריה · {when:%d/%m %H:%M}"
    return text


def picture(s: Signal) -> bytes:
    title = f"{s.symbol} · {TIMEFRAME} · {TITLES[s.strategy]}"
    marks = dict(
        direction=s.direction, breakout=_iso(s.time), plan=s.plan,
        min_bars=max(PICTURE_MIN_BARS, s.i - s.session_from + 1), time_format="%H:%M", after=s.after,
    )
    if s.line:
        marks.update(line=s.line, legend=((LINES[s.strategy][1], alert_chart.LINE),))
    if s.zone:
        start = _iso(s.candles[s.level_from].time)
        return alert_chart.render(s.candles, title, s.zone[0], s.zone[1], start, zone_colors=alert_chart.GOLD, **marks)
    if s.level is not None:
        marks.update(level=(s.level, _iso(s.candles[s.level_from].time)))
    return alert_chart.render(s.candles, title, None, None, None, **marks)


def send_alert(s: Signal, example: bool = False) -> bool:
    """The chart picture with the caption — or the caption alone if the picture can't be
    drawn. The trade is watched from then on, unless it's an example from the past."""
    try:
        png = picture(s)
    except Exception:
        logger.exception("Misc strategies: chart picture failed for %s, sending text only", s.key)
        png = None
    return trade_tracker.send_alert(png, format_message(s, example), BOT, s.key, s.symbol, None, None if example else s.plan)


def run_misc_strategies(db: Session) -> int:
    """Checks the 5m candles that just closed on every stock and sends each new signal;
    returns how many went out. Outside the hours a candle can signal in, nothing is fetched."""
    if not SESSION_OPEN < _clock(int(time.time())) <= (LAST_HOUR[0], LAST_HOUR[1] + MAX_AGE_SECONDS // 60):
        return 0
    with _run_lock:
        symbols = list(dict.fromkeys([*LARGE_CAPS, *movers()]))
        fresh = []
        now = time.time()
        for symbol, candles in fetch_all(symbols).items():
            s = build_series(candles)
            for i in range(max(0, len(candles) - 2), len(candles)):
                if now - (candles[i].time + BAR_SECONDS) <= MAX_AGE_SECONDS:
                    fresh += signals_at(s, i, symbol)
        fresh.sort(key=lambda x: x.time, reverse=True)
        return _send_new(db, fresh)


def _send_new(db: Session, fresh: list[Signal]) -> int:
    """Sends the signals not sent before, up to MAX_PER_RUN. One whose send fails isn't
    recorded, so the next run tries it again."""
    already = {k for (k,) in db.query(SentAlert.key).filter(SentAlert.key.in_([s.key for s in fresh]))}
    sent = 0
    for s in fresh:
        if s.key in already or sent >= MAX_PER_RUN:
            continue
        if sent:
            time.sleep(SEND_INTERVAL_SECONDS)
        if send_alert(s):
            db.add(SentAlert(key=s.key, symbol=s.symbol))
            db.commit()
            already.add(s.key)
            sent += 1
    if sent:
        logger.info("Misc strategies: %d alerts sent", sent)
    return sent


if __name__ == "__main__":
    per_strategy = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    found = [x for symbol, candles in fetch_all(LARGE_CAPS, HISTORY_PERIOD).items() for x in history_signals(symbol, candles)]
    found.sort(key=lambda x: x.time)
    for strategy in STRATEGIES:
        mine = [x for x in found if x.strategy == strategy]
        print(f"{strategy}: {len(mine)} signals")
        for x in mine[-per_strategy:]:
            print(" ", x.symbol, _iso(x.time), x.direction, "sent" if send_alert(x, example=True) else "FAILED")
            time.sleep(SEND_INTERVAL_SECONDS)
