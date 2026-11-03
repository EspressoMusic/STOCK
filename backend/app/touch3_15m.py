"""Telegram alerts for forex pairs that come to the same zone a third time, on 15-minute
candles — the user's ask (2/11/26), the one strategy of the 1h bot ("פורקס המשך") in
place of the 15m continuation (continuation_15m.py, code kept):

price turned at a zone twice from the same side — two swing highs, or two swing lows
(zones.py's clustering, on one kind of pivot only; the user's correction, 2/11/26: "at the
top or three times at the bottom", not highs and lows mixed) — each touch at most
MAX_GAP_BARS after the one before, never closing far through it in between, and now it
comes back to it from that side — on the third touch, as
the user picked for the Nasdaq's model 2 ("already on the third", nasdaq_scalp.py), not
after three. The alert comes on the visit's entry candle (trade_plan.zone_entry): from
below a short, from above a long; the stop behind the zone and the visit's wicks, 1:3.
Only when the daily candle agrees (daily_candle.py), and not while ranging between this
zone and an as strong one close by (is_ranging).

Runs every 15 minutes, on the candle closes (scheduler.py), and on demand; every setup is
sent once, remembered in the sent_alerts table. `python -m app.touch3_15m N` sends the
last N setups of the past 60 days, marked as examples — for the user to judge it by."""
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
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
from .nasdaq_scalp import recent_touches
from .technical_scan import ALERT_FOREX, _round_sig
from .trade_plan import TradePlan, is_entry_candle, zone_entry, zone_plan
from .zone_touch_scan import is_ranging, send_new
from .zones import RETEST_REACH, TOLERANCE, Zone, _cluster_zones, _find_pivots, detect_zones, typical_candle

logger = logging.getLogger(__name__)

TIMEFRAME = "15m"
BAR_SECONDS = 900
UNIVERSE = ALERT_FOREX  # the watchlist pairs, as the other forex bots
BOT = "1h"  # telegram.bot_chat — the "פורקס המשך" bot
TAG = "15m"
LIVE_PERIOD = "1mo"  # the window, plus the two weeks of daily candles daily_candles reads
HISTORY_PERIOD = "60d"  # Yahoo's limit for 15m bars
# Zones from the last two days' candles, so every touch is recent.
WINDOW_BARS = 192
# The alert comes on the third touch: the zone has exactly this many before it.
PRIOR_TOUCHES = 2
# Each touch at most this many candles (8 hours) after the one before. On the past 60
# days (20 pairs, 1:3, before the spread), wick touches from the same side: ~12 setups a
# day, win 26%, +0.04R a trade (shorts +68R, longs -47R); with the chart's 3-candle swings
# only ~6 a day, +0.05R; highs and lows mixed ~13 a day, -0.03R. 4 hours (in a one-day
# window, mixed) gave ~19 a day at -0.09R.
MAX_GAP_BARS = 32
# A touch is any wick into the zone that turns: a swing high/low of this many candles on
# each side (the chart's zones take 3) — the user's pick, 2/11/26, after a EUR/AUD box
# with three wicks into it the bot didn't count.
TOUCH_PIVOT_BARS = 1
# Touches are separate visits only when price got this many typical candles clear of the
# zone between them (as far as a visit reaches, RETEST_REACH); wicks of one visit are one
# touch. Half a candle: ~6 setups a day, ≈0R.
LEAVE = 0.25
# Price may close past the zone's far side by this many typical candles and the zone
# still holds — more is a break. A whole candle: ~13 a day, +0.07R.
STRAY = 0.5
# Ranging: an as strong zone ahead in the trade's direction, its middle within this
# share of price — a few typical 15m candles of a pair. Without it, twice the setups
# and -0.12R a trade.
RANGE_DISTANCE = 0.002
# A setup is sent only while its candle closed at most this long ago — a 15m alert that
# comes late (the server was down, Yahoo lagged) is no use.
MAX_AGE_SECONDS = 2 * BAR_SECONDS
SEND_INTERVAL_SECONDS = 1.1


@dataclass
class TouchSetup:
    symbol: str
    name: Optional[str]
    direction: str  # "down" = came up to the zone from below (short) | "up" = from above (long)
    zone_bottom: float
    zone_top: float
    touch_times: list[str]  # the two touches before, then the visit's first candle unless it's the entry
    time: str  # the entry candle's (ISO); the alert goes out when it closes
    candles: list[Candle] = field(default_factory=list, repr=False)  # for the alert's picture
    plan: Optional[TradePlan] = None  # entered on the last candle
    after: list[Candle] = field(default_factory=list, repr=False)  # an example's: the rest of its day

    @property
    def key(self) -> str:
        return f"touch3:{TIMEFRAME}:{self.symbol}:{self.time}"


_run_lock = threading.Lock()


def _clear(window: Sequence[Candle], zone: Zone, side: str, i: int, j: int) -> bool:
    """Whether a candle between i and j (both out) stood LEAVE clear of the zone, on its side."""
    gap = LEAVE * zone.unit
    return any(c.high < zone.bottom - gap if side == "down" else c.low > zone.top + gap for c in window[i + 1 : j])


def side_zones(window: Sequence[Candle]) -> list[tuple[str, Zone]]:
    """Zones of swing highs only — price came up to them and turned down: a short's
    ("down") — and of swing lows only (a long's, "up"); their touches one per visit, the
    most touched first."""
    candle = typical_candle(window)
    if not candle > 0:
        return []
    pivots = _find_pivots(window, TOUCH_PIVOT_BARS)
    out = []
    for direction, kind in (("down", "high"), ("up", "low")):
        same = [p for p in pivots if p[1] == getattr(window[p[0]], kind)]
        for z in _cluster_zones(window, same, candle, PRIOR_TOUCHES, TOLERANCE):
            visits: list[int] = []
            for k in z.touch_idx:
                if not visits or _clear(window, z, direction, visits[-1], k):
                    visits.append(k)
            if len(visits) >= PRIOR_TOUCHES:
                out.append((direction, replace(z, touch_idx=visits)))
    return sorted(out, key=lambda dz: -len(dz[1].touch_idx))


def find_setup(window: Sequence[Candle], ranging: bool = True) -> Optional[tuple[str, Zone, int]]:
    """(direction, zone, the visit's first candle) when the window's last candle is the
    entry on a zone's third touch from the same side; the zone comes back with its two
    touches only. The most touched zone first. ranging=False skips the ranging check
    (ema_touch3_5m.py's rules have none)."""
    # zone_entry checks this too; first, as it's cheap and the zones aren't.
    if len(window) < 3 or not any(is_entry_candle(window[-2], window[-1], d) for d in ("down", "up")):
        return None
    recent = None
    for side, zone in side_zones(window):
        found = zone_entry(window, zone.bottom, zone.top, RETEST_REACH * zone.unit)
        if not found or found[0] != side:
            continue
        direction, a = found
        touches = recent_touches(replace(zone, touch_idx=[k for k in zone.touch_idx if k < a]), a, MAX_GAP_BARS)
        if len(touches) != PRIOR_TOUCHES or not _clear(window, zone, side, touches[-1], a):
            continue
        # Price stayed on its side: no close far through the zone since the first touch.
        stray = STRAY * zone.unit
        if any(c.close > zone.top + stray if side == "down" else c.close < zone.bottom - stray for c in window[touches[0] : a]):
            continue
        zone = replace(zone, start_idx=touches[0], touch_idx=touches)
        if not ranging:
            return direction, zone, a
        if recent is None:
            recent = [
                replace(z, touch_idx=recent_touches(z, len(window) - 1, MAX_GAP_BARS))
                for z in detect_zones(window, min_touches=PRIOR_TOUCHES)
            ]
        if is_ranging(window, recent, zone, direction, RANGE_DISTANCE):
            return None
        return direction, zone, a
    return None


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def setup_from(
    symbol: str, name: Optional[str], window: Sequence[Candle], daily: Sequence[Candle]
) -> Optional[TouchSetup]:
    """The alert on the last of these closed 15m candles; daily = the closed daily
    candles before its day."""
    found = find_setup(window)
    if found is None or not confirms(daily, found[0]):
        return None
    direction, zone, a = found
    trade = zone_plan(window, a, direction, zone.bottom, zone.top, zone.unit)
    if trade is None:
        return None
    last = len(window) - 1
    return TouchSetup(
        symbol=symbol,
        name=name,
        direction=direction,
        zone_bottom=_round_sig(zone.bottom),
        zone_top=_round_sig(zone.top),
        # The entry candle gets the arrow; a touch's dot there would cover it.
        touch_times=[_iso(window[k].time) for k in [*zone.touch_idx, *([a] if a < last else [])]],
        time=_iso(window[last].time),
        candles=list(window),
        plan=trade,
    )


def _fetch(symbol: str, period: str) -> Optional[pd.DataFrame]:
    try:
        df = yf.Ticker(symbol).history(interval=TIMEFRAME, period=period)
    except Exception:
        logger.warning("Third touch 15m scan: history fetch failed for %s", symbol)
        return None
    return None if df is None or df.empty else df


def _evaluate(symbol: str, name: Optional[str]) -> Optional[TouchSetup]:
    df = _fetch(symbol, LIVE_PERIOD)
    if df is None:
        return None
    candles = _to_candles(df)
    # Yahoo includes the candle still in progress; the entry counts on a closed one.
    if candles and candles[-1].time + BAR_SECONDS > time.time():
        candles.pop()
    if not candles or time.time() - (candles[-1].time + BAR_SECONDS) > MAX_AGE_SECONDS:
        return None
    return setup_from(symbol, name, candles[-WINDOW_BARS:], daily_candles(df))


def scan() -> list[TouchSetup]:
    results: list[TouchSetup] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate, sym, name) for sym, name in UNIVERSE]
        for fut in as_completed(futures):
            if (result := fut.result()) is not None:
                results.append(result)
    return results


def history_setups(symbol: str, name: Optional[str], df: pd.DataFrame) -> list[TouchSetup]:
    """Every setup the live run would have sent on these 15m bars, oldest first."""
    candles = _to_candles(df)
    days = pd.Series([_forex_day(ts) for ts in df.index], index=df.index)
    out = []
    for i in range(WINDOW_BARS, len(candles)):
        window = candles[i + 1 - WINDOW_BARS : i + 1]
        if find_setup(window) is None:
            continue
        day = _forex_day(pd.Timestamp(window[-1].time, unit="s", tz="UTC"))
        if s := setup_from(symbol, name, window, daily_candles(df[days < day])):
            s.after = alert_chart.rest_of_day(candles, i)
            out.append(s)
    return out


def format_message(s: TouchSetup, example: bool = False) -> str:
    text = telegram.alert_caption(s.symbol, s.name, s.direction == "up", tag=TAG)
    if example:
        when = datetime.fromisoformat(s.time) + timedelta(seconds=BAR_SECONDS)
        text += f"\n🕘 דוגמה מההיסטוריה · {when.astimezone(ZoneInfo(settings.alert_timezone)):%d/%m %H:%M}"
    return text


def send_alert(s: TouchSetup, example: bool = False) -> bool:
    """The chart picture with the message as its caption — or the message alone if the
    picture can't be drawn. Its trade is watched from then on (trade_tracker.py), unless
    it's an example from the past."""
    try:
        png = alert_chart.render(
            s.candles, f"{s.name or s.symbol} · {TIMEFRAME}", s.zone_bottom, s.zone_top, s.touch_times[0],
            direction=s.direction, breakout=s.time, touches=s.touch_times, plan=s.plan,
            zone_colors=alert_chart.GOLD, after=s.after,
        )
    except Exception:
        logger.exception("Third touch 15m alert: chart picture failed for %s, sending text only", s.symbol)
        png = None
    return trade_tracker.send_alert(png, format_message(s, example), BOT, s.key, s.symbol, s.name, None if example else s.plan)


def run_touch3_alerts(db: Session) -> int:
    """Scans the 15m candle that just closed and sends each new setup; returns how many went out."""
    with _run_lock:
        found = scan()
        if not telegram.is_configured():
            logger.info("Third touch 15m alerts: Telegram not configured, %d found but not sent", len(found))
            return 0
        sent = send_new(db, found, send_alert, len(found))
        logger.info("Third touch 15m alerts: %d setups in %d pairs, %d sent", len(found), len(UNIVERSE), sent)
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
