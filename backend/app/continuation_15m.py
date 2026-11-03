"""Telegram alerts for forex pairs that broke cleanly out of a small 1h base and then, on
15-minute candles, came back to it and broke away from it twice — the user's ask
(29/10/26, their example GBP/CAD on 28/10/26), on the 1h bot ("פורקס המשך") next to
"אסטרטגיה 1" (retest_alerts.py), the caption tagged "15m". For a short:

1. 1h: a pause of BASE_BARS small candles — their high to low is the zone, at most
   BASE_MAX_HEIGHT typical candles tall — then a red candle with a body of at least
   BREAK_BODY closing under it, and under every low of the CLEAN_BARS candles before: it
   breaks into clean space, not into chop (the user's "במקום נקי ללא דשדוש ונגיעות").
2. 15m: price closes back inside the zone, a red candle closes under it (the first
   break), price closes back inside again, and a red entry candle (trade_plan.py) closes
   under it again — the alert, on that candle: the stop behind the zone and the second
   return's wicks, 1:3. A close past the zone's far side ends the setup, and so does
   MAX_SETUP_BARS (two hours) without the second break — the user wants it all close to
   the breakout, not spread over the day.

A long is the mirror. Only when the daily candle agrees (daily_candle.py). The picture is
the 1h chart (the breakout's arrow) beside the 15m one (the entry's arrow and the trade),
each with the zone alone, in gold — the user asked for one zone and few marks. Runs every 15 minutes, on the candle closes
(scheduler.py), and on demand; every setup is sent once, remembered in the sent_alerts
table. `python -m app.continuation_15m N` sends the last N setups of the past 60 days,
marked as examples — for the user to judge the strategy by."""
import bisect
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from sqlalchemy.orm import Session

from . import alert_chart, telegram, trade_tracker
from .candles import Candle, _to_candles
from .config import settings
from .daily_candle import _forex_day, confirms, daily_candles
from .technical_scan import ALERT_FOREX, _round_sig
from .trade_plan import TradePlan, is_entry_candle, zone_plan
from .zone_touch_scan import send_new
from .zones import BREAK_BUFFER, typical_candle

logger = logging.getLogger(__name__)

TIMEFRAME = "15m"
BAR_SECONDS = 900
HOUR = 3600
UNIVERSE = ALERT_FOREX  # the watchlist pairs, as the other forex bots
BOT = "1h"  # telegram.bot_chat — "אסטרטגיה 1"'s bot
TAG = "15m"
LIVE_PERIOD = "1mo"  # the 1h candles' typical size, plus the two weeks daily_candles reads
HISTORY_PERIOD = "60d"  # Yahoo's limit for 15m bars
# The pause: the 1h candles right before the breakout (the user's example had two), and
# how tall they may be together, in typical 1h candles.
BASE_BARS = 2
BASE_MAX_HEIGHT = 1.5
# The breakout candle's body, in typical 1h candles.
BREAK_BODY = 1.0
# The breakout closes past every low (high, for a long) of this many 1h candles before it.
CLEAN_BARS = 8
TYPICAL_BARS = 200  # the 1h candles a typical candle is measured on
# The second break has to come within this many 15m candles (two hours) of the 1h breakout.
MAX_SETUP_BARS = 8
# A setup is sent only while its candle closed at most this long ago — a 15m alert that
# comes late (the server was down, Yahoo lagged) is no use.
MAX_AGE_SECONDS = 2 * BAR_SECONDS
# Candles shown on each side of the picture, at least; on the 15m side, also LEAD_BARS
# before the base.
SHOWN_BARS = 40
LEAD_BARS = 8
SEND_INTERVAL_SECONDS = 1.1


@dataclass
class Base:
    idx: int  # the 1h candle that broke out of it
    dir: str  # "down" | "up"
    bottom: float
    top: float
    unit: float  # one typical 1h candle, in price


@dataclass
class ContinuationSetup:
    symbol: str
    name: Optional[str]
    direction: str  # "down" = short | "up" = long
    zone_bottom: float
    zone_top: float
    zone_start_time: str  # the base's first 1h candle
    breakout_time: str  # the 1h candle that broke out of the base
    time: str  # the entry candle's (15m, ISO); the alert goes out when it closes
    key: str  # one alert per 1h breakout
    candles: list[Candle] = field(default_factory=list, repr=False)  # 15m, for the alert's picture
    hours: list[Candle] = field(default_factory=list, repr=False)  # 1h, closed by the entry's close
    plan: Optional[TradePlan] = None  # entered on the last candle
    after: list[Candle] = field(default_factory=list, repr=False)  # an example's: the rest of its day


_run_lock = threading.Lock()


def find_base(h1: Sequence[Candle], k: int) -> Optional[Base]:
    """The base the 1h candle k broke out of, if it's a clean breakout (step 1)."""
    if k < max(BASE_BARS, CLEAN_BARS):
        return None
    c = h1[k]
    base = h1[k - BASE_BARS : k]
    bottom, top = min(x.low for x in base), max(x.high for x in base)
    unit = typical_candle(h1[max(0, k - TYPICAL_BARS) : k]) * c.close
    if not unit > 0 or top - bottom > BASE_MAX_HEIGHT * unit or abs(c.close - c.open) < BREAK_BODY * unit:
        return None
    before = h1[k - CLEAN_BARS : k]
    buffer = BREAK_BUFFER * unit
    if c.close < c.open and c.close < bottom - buffer and c.close < min(x.low for x in before):
        return Base(k, "down", bottom, top, unit)
    if c.close > c.open and c.close > top + buffer and c.close > max(x.high for x in before):
        return Base(k, "up", bottom, top, unit)
    return None


def second_break(m15: Sequence[Candle], start: int, base: Base) -> Optional[tuple[int, int]]:
    """(the second return's first candle, the second break) on the 15m candles from start,
    the first one after the 1h breakout (step 2); None if the setup ended first."""
    down = base.dir == "down"
    buffer = BREAK_BUFFER * base.unit
    returns = breaks = 0
    back = start
    for j in range(max(start, 1), min(len(m15), start + MAX_SETUP_BARS)):
        c = m15[j]
        if (c.close > base.top + buffer) if down else (c.close < base.bottom - buffer):
            return None
        if returns == breaks:
            if base.bottom <= c.close <= base.top:
                returns += 1
                back = j
            continue
        if down:
            broke = c.close < c.open and c.close < base.bottom - buffer
        else:
            broke = c.close > c.open and c.close > base.top + buffer
        if not broke:
            continue
        if returns < 2:
            breaks += 1
        elif is_entry_candle(m15[j - 1], c, base.dir):
            return back, j
    return None


def find_setups(h1: Sequence[Candle], m15: Sequence[Candle], since: int = 0) -> list[tuple[Base, int, int]]:
    """(base, second return, entry candle) of every setup on these candles whose 1h
    breakout closed at or after since (unix), oldest first."""
    times = [c.time for c in m15]
    out = []
    for k in range(len(h1)):
        if h1[k].time + HOUR < since or (base := find_base(h1, k)) is None:
            continue
        found = second_break(m15, bisect.bisect_left(times, h1[k].time + HOUR), base)
        if found:
            out.append((base, *found))
    return out


def hourly(df: pd.DataFrame) -> list[Candle]:
    """The 1h candles of these 15m bars."""
    return _to_candles(df.resample("1h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna())


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def setup_from(
    symbol: str, name: Optional[str], h1: Sequence[Candle], m15: Sequence[Candle], found: tuple[Base, int, int],
    daily: Sequence[Candle],
) -> Optional[ContinuationSetup]:
    """The alert on the 15m entry candle found; daily = the closed daily candles before its day."""
    base, back, j = found
    if not confirms(daily, base.dir):
        return None
    candles = m15[: j + 1]
    # The stop behind the zone and the second return's wicks.
    trade = zone_plan(candles, back, base.dir, base.bottom, base.top, base.unit)
    if trade is None:
        return None
    breakout_time = _iso(h1[base.idx].time)
    return ContinuationSetup(
        symbol=symbol,
        name=name,
        direction=base.dir,
        zone_bottom=_round_sig(base.bottom),
        zone_top=_round_sig(base.top),
        zone_start_time=_iso(h1[base.idx - BASE_BARS].time),
        breakout_time=breakout_time,
        time=_iso(candles[-1].time),
        key=f"cont15:{symbol}:{breakout_time}",
        candles=list(candles),
        hours=[c for c in h1 if c.time + HOUR <= candles[-1].time + BAR_SECONDS],
        plan=trade,
    )


def _fetch(symbol: str, period: str) -> Optional[pd.DataFrame]:
    try:
        df = yf.Ticker(symbol).history(interval=TIMEFRAME, period=period)
    except Exception:
        logger.warning("Continuation 15m scan: history fetch failed for %s", symbol)
        return None
    return None if df is None or df.empty else df


def _evaluate(symbol: str, name: Optional[str]) -> Optional[ContinuationSetup]:
    df = _fetch(symbol, LIVE_PERIOD)
    if df is None:
        return None
    # Yahoo includes the candle still in progress; the entry counts on a closed one.
    if df.index[-1].timestamp() + BAR_SECONDS > time.time():
        df = df.iloc[:-1]
    m15 = _to_candles(df)
    if not m15 or time.time() - (m15[-1].time + BAR_SECONDS) > MAX_AGE_SECONDS:
        return None
    end = m15[-1].time + BAR_SECONDS
    h1 = [c for c in hourly(df) if c.time + HOUR <= end]
    since = end - (MAX_SETUP_BARS + 1) * BAR_SECONDS - HOUR
    found = [f for f in find_setups(h1, m15, since) if f[2] == len(m15) - 1]
    return setup_from(symbol, name, h1, m15, found[-1], daily_candles(df)) if found else None


def scan() -> list[ContinuationSetup]:
    results: list[ContinuationSetup] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate, sym, name) for sym, name in UNIVERSE]
        for fut in as_completed(futures):
            if (result := fut.result()) is not None:
                results.append(result)
    return results


def history_setups(symbol: str, name: Optional[str], df: pd.DataFrame) -> list[ContinuationSetup]:
    """Every setup the live run would have sent on these 15m bars, oldest first."""
    m15 = _to_candles(df)
    h1 = hourly(df)
    days = pd.Series([_forex_day(ts) for ts in df.index], index=df.index)
    out = []
    for found in find_setups(h1, m15):
        day = _forex_day(pd.Timestamp(m15[found[2]].time, unit="s", tz="UTC"))
        if s := setup_from(symbol, name, h1, m15, found, daily_candles(df[days < day])):
            s.after = alert_chart.rest_of_day(m15, found[2])
            out.append(s)
    return out


def format_message(s: ContinuationSetup, example: bool = False) -> str:
    text = telegram.alert_caption(s.symbol, s.name, s.direction == "up", tag=TAG)
    if example:
        when = datetime.fromisoformat(s.time) + timedelta(seconds=BAR_SECONDS)
        text += f"\n🕘 דוגמה מההיסטוריה · {when.astimezone(ZoneInfo(settings.alert_timezone)):%d/%m %H:%M}"
    return text


def send_alert(s: ContinuationSetup, example: bool = False) -> bool:
    """The 1h and 15m charts side by side with the message as their caption — or the
    message alone if the picture can't be drawn. Its trade is watched from then on (trade_tracker.py), unless
    it's an example from the past."""
    start = int(datetime.fromisoformat(s.zone_start_time).timestamp())
    shown = max(SHOWN_BARS, sum(1 for c in s.candles if c.time >= start) + LEAD_BARS)
    zone = (s.zone_bottom, s.zone_top, s.zone_start_time)
    title = s.name or s.symbol
    try:
        png = alert_chart.render_side_by_side(
            ((s.hours, f"{title} · 1h", *zone), dict(
                direction=s.direction, breakout=s.breakout_time, min_bars=SHOWN_BARS, zone_colors=alert_chart.GOLD,
            )),
            ((s.candles, f"{title} · {TIMEFRAME}", *zone), dict(
                direction=s.direction, breakout=s.time, min_bars=shown, plan=s.plan, zone_colors=alert_chart.GOLD,
                time_format="%H:%M", after=s.after,  # the half picture is too narrow for the date too
            )),
        )
    except Exception:
        logger.exception("Continuation 15m alert: chart picture failed for %s, sending text only", s.symbol)
        png = None
    return trade_tracker.send_alert(png, format_message(s, example), BOT, s.key, s.symbol, s.name, None if example else s.plan)


def run_continuation_alerts(db: Session) -> int:
    """Scans the 15m candle that just closed and sends each new setup; returns how many went out."""
    with _run_lock:
        found = scan()
        if not telegram.is_configured():
            logger.info("Continuation 15m alerts: Telegram not configured, %d found but not sent", len(found))
            return 0
        sent = send_new(db, found, send_alert, len(found))
        logger.info("Continuation 15m alerts: %d setups in %d pairs, %d sent", len(found), len(UNIVERSE), sent)
        return sent


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    found = []
    for sym, name in UNIVERSE:
        df = _fetch(sym, HISTORY_PERIOD)
        if df is not None:
            found += history_setups(sym, name, df)
    found.sort(key=lambda s: s.time)
    print(f"{len(found)} setups in the past 60 days ({sum(s.direction == 'down' for s in found)} shorts)")
    for s in found[-count:] if count else []:
        print(" ", s.time, s.symbol, s.direction, "sent" if send_alert(s, example=True) else "FAILED")
        time.sleep(SEND_INTERVAL_SECONDS)
