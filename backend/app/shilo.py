"""SHILO — the stocks bot's scalping strategy (the user's ask, 2/11/26, named by them), on
5-minute US stock candles. Of the readings offered the user picked the stocks bot
(TELEGRAM_STOCKS_BOT_TOKEN, idle since 30/10/26) for it alone, and the entry candle.

From their MRVL pictures (29-30/10/26): price breaks the last low and moves on, not stuck
at it; then comes back to that same point with the Stochastic RSI overbought — a short
on the entry candle there (trade_plan.is_entry_candle: red, closing under the body before
it). The mirror: broke the last high and moved on, came back down to it with the Stochastic
RSI oversold — a long.

- The point: a swing low (high) — under the PIVOT_BARS candles on each side — of the
  same session.
- The break: the first close past it (BREAK_BUFFER typical candles); within MOVE_BARS
  price gets MIN_MOVE typical candles past it — or the level is dropped.
- The return: price comes back to it (within REACH); the entry candle on that visit or
  right after it, with the Stochastic RSI (14, 14, 3) %K over 80 (under 20 for a long) on
  it. A close FAIL typical candles back past the point's body ends the setup.
- The stop behind the point and the visit's wicks; 1:3, as on the other trade bots.

The stocks and the session hours are the "שונות" bot's (misc_strategies.py): ~100 big
Nasdaq names and the day's movers, no entry in the session's last hour; and only while the
stock is in play — trading MIN_RVOL times the usual volume by then (1.7, not the "שונות"
bot's 2: the user's MRVL 30/10 was at 1.9). On the past 60 days of the big names that's
~6 signals a day, win ~28% at 1:3 (~+0.11R a trade, before costs; 1:2 came to +0.09R).
Checked after each 5m candle closes (scheduler.py); the trade is watched until its target
or stop (trade_tracker.py). `python -m app.shilo N` sends the last N signals of the past
60 days, marked as examples."""
import logging
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import takewhile
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy.orm import Session

from . import alert_chart, telegram, trade_tracker
from .candles import Candle
from .config import settings
from .misc_strategies import (
    BAR_SECONDS, HISTORY_PERIOD, LARGE_CAPS, LAST_HOUR, MAX_AGE_SECONDS, SEND_INTERVAL_SECONDS,
    SESSION_OPEN, TIMEFRAME, build_series, fetch_all, movers,
)
from .models import SentAlert
from .technical_scan import _stoch_rsi
from .trade_plan import TradePlan, behind, is_entry_candle, plan

logger = logging.getLogger(__name__)

BOT = "stocks"
NAME = "SHILO"
NY = ZoneInfo("America/New_York")
REWARD_RISK = 3
MIN_RVOL = 1.7
# The typical candle: the median high-low of the last TYPICAL_BARS.
TYPICAL_BARS = 50
PIVOT_BARS = 2
BREAK_BUFFER = 0.1
# "Moves, not stuck": MIN_MOVE typical candles past the point within MOVE_BARS of the break
# (1.5 keeps the user's MRVL 30/10 example, which fell ~2).
MIN_MOVE = 1.5
MOVE_BARS = 6
REACH = 0.25
FAIL = 1.0
# The setup lasts at most this many candles from the break (four hours), within its session.
MAX_BARS = 48
STOCH_OVERSOLD = 20.0
STOCH_OVERBOUGHT = 80.0
# A stop nearer than this many typical candles is noise.
MIN_RISK = 0.3
MAX_PER_RUN = 10
PICTURE_MIN_BARS = 30


@dataclass
class Signal:
    symbol: str
    direction: str  # "up" = long | "down" = short
    i: int  # the entry candle's index; in on its close
    level: float  # the point: the swing low (high) broken
    pivot: int  # its candle
    plan: TradePlan
    session_from: int = 0
    candles: list[Candle] = field(default_factory=list, repr=False)  # up to the entry candle

    @property
    def time(self) -> int:
        return self.candles[self.i].time

    @property
    def key(self) -> str:
        return f"shilo:{self.symbol}:{self.direction}:{self.time}"


_run_lock = threading.Lock()


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _clock(ts: int) -> tuple[int, int]:
    t = datetime.fromtimestamp(ts, NY)
    return t.hour, t.minute


def typical(candles: Sequence[Candle]) -> list[float]:
    ranges = [c.high - c.low for c in candles]
    return [statistics.median(ranges[max(0, i - TYPICAL_BARS + 1) : i + 1]) for i in range(len(candles))]


def _setup(candles: Sequence[Candle], k: Sequence[float], typ: Sequence[float], day_start: Sequence[int],
           j: int, direction: str) -> Optional[tuple[int, int]]:
    """(the entry candle, the visit's first) of the setup on the swing point at j, if it
    gets one — each step seeing only the candles up to it."""
    n, up = len(candles), direction == "up"
    pivot = candles[j]
    level = pivot.high if up else pivot.low
    body = max(pivot.open, pivot.close) if up else min(pivot.open, pivot.close)
    session = list(takewhile(lambda x: day_start[x] == day_start[j], range(j + PIVOT_BARS + 1, n)))
    b = next((x for x in session if (
        candles[x].close > level + BREAK_BUFFER * typ[x] if up else candles[x].close < level - BREAK_BUFFER * typ[x]
    )), None)
    if b is None:
        return None
    away = level + MIN_MOVE * typ[b] if up else level - MIN_MOVE * typ[b]
    m = next((x for x in session if b <= x < b + MOVE_BARS and (candles[x].high >= away if up else candles[x].low <= away)), None)
    if m is None:
        return None
    visit, was_at = None, False  # the visit's first candle; whether the candle before was at the point
    for x in (x for x in session if m < x < b + MAX_BARS):
        c, t = candles[x], typ[x]
        if c.close < body - FAIL * t if up else c.close > body + FAIL * t:
            return None
        at = c.low <= level + REACH * t if up else c.high >= level - REACH * t
        if at and not was_at:
            visit = x
        extreme = k[x] < STOCH_OVERSOLD if up else k[x] > STOCH_OVERBOUGHT
        if (at or was_at) and extreme and is_entry_candle(candles[x - 1], c, direction):
            return x, visit
        was_at = at
    return None


def find_signals(symbol: str, candles: Sequence[Candle], k: Optional[Sequence[float]] = None) -> list[Signal]:
    """Every signal on these candles, oldest first — one a candle and side; k is the
    Stochastic RSI's %K (computed when not given). None from the pre-market, in the
    session's last hour or while the stock isn't in play."""
    candles = list(candles)
    if k is None:
        k = _stoch_rsi(pd.Series([c.close for c in candles]))[0].tolist()
    s = build_series(candles)
    typ = typical(candles)
    found: dict[tuple[int, str], Signal] = {}
    for j in range(PIVOT_BARS, len(candles) - PIVOT_BARS):
        around = candles[j - PIVOT_BARS : j + PIVOT_BARS + 1]
        for direction in ("down", "up"):
            up = direction == "up"
            if (candles[j].high < max(c.high for c in around)) if up else (candles[j].low > min(c.low for c in around)):
                continue
            got = _setup(candles, k, typ, s.day_start, j, direction)
            if got is None:
                continue
            i, a = got
            t = candles[i].time
            if (i, direction) in found or not SESSION_OPEN <= _clock(t) or _clock(t + BAR_SECONDS) > LAST_HOUR:
                continue
            if not s.rvol[i] >= MIN_RVOL:
                continue
            level = candles[j].high if up else candles[j].low
            trade = plan(direction, candles[i].close, behind(candles[a : i + 1], direction, level, typ[i]), REWARD_RISK)
            if trade is None or abs(trade.entry - trade.stop) < MIN_RISK * typ[i]:
                continue
            found[(i, direction)] = Signal(symbol, direction, i, level, j, trade, s.day_start[i], candles[: i + 1])
    return sorted(found.values(), key=lambda x: x.i)


def format_message(s: Signal, example: bool = False) -> str:
    text = telegram.alert_caption(s.symbol, None, s.direction == "up", NAME)
    if example:
        when = datetime.fromtimestamp(s.time + BAR_SECONDS, ZoneInfo(settings.alert_timezone))
        text += f"\n🕘 דוגמה מההיסטוריה · {when:%d/%m %H:%M}"
    return text


def picture(s: Signal) -> bytes:
    """The point's line from its candle, the entry arrow, the trade and the Stochastic RSI
    under the candles — as on the user's pictures."""
    k, d = _stoch_rsi(pd.Series([c.close for c in s.candles]))
    return alert_chart.render(
        s.candles, f"{s.symbol} · {TIMEFRAME} · {NAME}", None, None, None,
        direction=s.direction, breakout=_iso(s.time), plan=s.plan, time_format="%H:%M",
        min_bars=max(PICTURE_MIN_BARS, s.i - s.session_from + 1),
        level=(s.level, _iso(s.candles[s.pivot].time)), stoch=(k.tolist(), d.tolist()),
    )


def send_alert(s: Signal, example: bool = False) -> bool:
    """The chart picture with the caption — or the caption alone if the picture can't be
    drawn. The trade is watched from then on, unless it's an example from the past."""
    try:
        png = picture(s)
    except Exception:
        logger.exception("SHILO: chart picture failed for %s, sending text only", s.key)
        png = None
    return trade_tracker.send_alert(png, format_message(s, example), BOT, s.key, s.symbol, None, None if example else s.plan)


def run_shilo(db: Session) -> int:
    """Checks the 5m candles that just closed on every stock and sends each new signal;
    returns how many went out. Outside the hours a candle can signal in, nothing is fetched."""
    if not SESSION_OPEN < _clock(int(time.time())) <= (LAST_HOUR[0], LAST_HOUR[1] + MAX_AGE_SECONDS // 60):
        return 0
    with _run_lock:
        symbols = list(dict.fromkeys([*LARGE_CAPS, *movers()]))
        now = time.time()
        fresh = [
            x for symbol, candles in fetch_all(symbols).items() for x in find_signals(symbol, candles)
            if now - (x.time + BAR_SECONDS) <= MAX_AGE_SECONDS
        ]
        fresh.sort(key=lambda x: x.time, reverse=True)
        already = {key for (key,) in db.query(SentAlert.key).filter(SentAlert.key.in_([x.key for x in fresh]))}
        sent = 0
        for x in fresh:
            if x.key in already or sent >= MAX_PER_RUN:
                continue
            if sent:
                time.sleep(SEND_INTERVAL_SECONDS)
            # One whose send fails isn't recorded, so the next run tries it again.
            if send_alert(x):
                db.add(SentAlert(key=x.key, symbol=x.symbol))
                db.commit()
                already.add(x.key)
                sent += 1
        if sent:
            logger.info("SHILO: %d alerts sent", sent)
        return sent


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    found = [x for symbol, candles in fetch_all(LARGE_CAPS, HISTORY_PERIOD).items() for x in find_signals(symbol, candles)]
    found.sort(key=lambda x: x.time)
    print(f"{len(found)} signals")
    for x in found[-count:]:
        print(" ", x.symbol, _iso(x.time), x.direction, "sent" if send_alert(x, example=True) else "FAILED")
        time.sleep(SEND_INTERVAL_SECONDS)
