"""Scalping alerts on the Nasdaq 100 index itself, on 5-minute candles during the US
session — the user's fourth bot ("נאסדק", TELEGRAM_NASDAQ_BOT_TOKEN). Three models:

- Model 1: a candle comes down onto the EMA50 and touches it, and the very next candle
  is green and closes above that candle's body → long. Up onto it and a red one closing
  below → short. The stop behind the candle on the line.
- Model 2: the third touch of a zone — price comes back to one it already turned at
  twice or more (the chart's own zones, zones.py) — then the entry candle
  (trade_plan.zone_entry), and not while ranging between it and an as strong zone close
  by (is_ranging). Coming down onto it → long. The stop behind the zone. And the
  Stochastic RSI at an extreme on the entry candle — oversold for a long, overbought for
  a short (the user's ask, 30/10/26).
- Model 3: model 2 on 1-minute candles, from the last two hours, touches at most a
  quarter of an hour apart (the user's ask, 27/10/26) — the pre-market's touches count
  too (28/10/26).

No model enters in the session's last hour (the user's rule, 1/11/26).

Yahoo's ^NDX is real time; its NQ futures run ~10 minutes late, too late to scalp on.
The pre-market, which ^NDX has no candles for, comes from QQQ (with_premarket).
Every alert comes with its trade, 1:3 (trade_plan.py). Checked right after each candle
closes — every 5 minutes, and every minute for model 3 (scheduler.py); each signal is
sent once, remembered in the sent_alerts table.
`python -m app.nasdaq_scalp N` sends the last N signals of each model from the past 60
days (7 for model 3), marked as examples — for the user to judge the models by."""
import logging
import statistics
import sys
import threading
import time
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
from .models import SentAlert
from .technical_scan import _stoch_rsi
from .trade_plan import TradePlan, behind, is_entry_candle, plan, zone_entry, zone_plan
from .zone_touch_scan import is_ranging
from .zones import RETEST_REACH, Zone, detect_zones, typical_candle

logger = logging.getLogger(__name__)

SYMBOL = "^NDX"
NAME = "NAS100"
TIMEFRAME = "5m"
BAR_SECONDS = 300
TRADINGVIEW_URL = "https://www.tradingview.com/chart/?symbol=NASDAQ%3ANDX"
LIVE_PERIOD = "5d"  # enough candles for the EMA50 to settle and for the zones' window
HISTORY_PERIOD = "60d"  # Yahoo's limit for 5m bars
EMA_PERIOD = 50
EMA_LEGEND = ((f"EMA {EMA_PERIOD}", alert_chart.LINE),)
# Zones come from the last two sessions' candles, so every touch is recent.
ZONE_WINDOW_BARS = 156
# The alert comes on this touch, so the zone needs one fewer before it (the user's
# pick, 27/10/26: on the third, not after three).
MIN_TOUCHES = 3
# Each touch has to come within this many candles (an hour) of the one before it — the
# user's pick (27/10/26): touches hours or days apart don't count.
MAX_GAP_BARS = 12
# Model 2's Stochastic RSI (14, 14, 3 — technical_scan.py's) on the entry candle: under
# this for a long, over the other for a short. On 60 days, 20 of the 75 signals were left
# — 5 hit the target, breaking even, against -6R without it.
STOCH_OVERSOLD = 20.0
STOCH_OVERBOUGHT = 80.0
# A candle reaching this many typical candles short of the zone already touches it.
ZONE_REACH = RETEST_REACH
# Ranging: an as strong zone ahead in the trade's direction, its middle within this
# share of price — about three typical 5m candles of the index.
RANGE_DISTANCE = 0.003
# A signal is sent only while its candle closed at most this long ago — a scalp alert
# that comes late (the server was down, Yahoo lagged) is no use.
MAX_AGE_SECONDS = 2 * BAR_SECONDS
SEND_INTERVAL_SECONDS = 1.1
# The models the bot sends live — model 2 only since 29/10/26, the user's call: one
# strategy per bot. The others still show in the history examples.
LIVE_MODELS = {2}

# Model 3, on 1-minute candles.
M1_TIMEFRAME = "1m"
M1_BAR_SECONDS = 60
M1_LIVE_PERIOD = "1d"
M1_HISTORY_PERIOD = "7d"  # Yahoo's limit for 1m bars
# Zones from the last two hours' candles, the pre-market's included, never from the day
# before.
M1_WINDOW_BARS = 120
SESSION_GAP_SECONDS = 3600
# QQQ's pre-market is real time, and times the day's NDX/QQQ ratio (about 41, steady to a
# few index points through a day) it draws what the index's chart does before the open.
PREMARKET_SYMBOL = "QQQ"
# The session's open (scan_timezone) — signals come from its candles only, not the
# pre-market's.
SESSION_OPEN = (9, 30)
# No entry from this time (scan_timezone) to the session's close, the hour before it: a
# signal candle closing then is no signal.
LAST_HOUR = (15, 0)
SESSION_CLOSE = (16, 0)
# Touches at most a quarter of an hour apart — 14 signals on 27/10/26, 3 at 10 minutes.
M1_MAX_GAP_BARS = 15
# About four typical 1m candles of the index.
M1_RANGE_DISTANCE = 0.001


@dataclass
class ScalpSignal:
    model: int  # 1 = EMA50 touch + body break, 2 = 3-touch zone, 3 = 3-touch zone on 1m
    direction: str  # "up" = long | "down" = short
    time: str  # the signal candle's (ISO); the alert goes out when it closes
    # Model 1: the candle on the EMA; 2 and 3: the zone's touches, then this visit's first
    # candle unless it's the signal candle.
    touch_times: list[str]
    zone_bottom: Optional[float] = None
    zone_top: Optional[float] = None
    candles: list[Candle] = field(default_factory=list, repr=False)  # up to the signal candle
    ema: list[float] = field(default_factory=list, repr=False)  # aligned with candles
    after: list[Candle] = field(default_factory=list, repr=False)  # an example's: the rest of its day
    plan: Optional[TradePlan] = None  # entered on the signal candle

    @property
    def key(self) -> str:
        return f"nasdaq:m{self.model}:{self.time}"

    @property
    def timeframe(self) -> str:
        return M1_TIMEFRAME if self.model == 3 else TIMEFRAME

    @property
    def bar_seconds(self) -> int:
        return M1_BAR_SECONDS if self.model == 3 else BAR_SECONDS


_run_lock = threading.Lock()


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def ema_series(candles: Sequence[Candle], period: int = EMA_PERIOD) -> list[float]:
    """As the chart and technical_scan.py draw it (pandas ewm, adjust=False)."""
    return pd.Series([c.close for c in candles]).ewm(span=period, adjust=False).mean().tolist()


def stoch_series(candles: Sequence[Candle]) -> list[float]:
    """The Stochastic RSI's %K, aligned with candles (NaN until it settles)."""
    return _stoch_rsi(pd.Series([c.close for c in candles]))[0].tolist()


def in_last_hour(close: int) -> bool:
    """A candle closing at this time (unix) would enter in the session's last hour."""
    t = datetime.fromtimestamp(close, ZoneInfo(settings.scan_timezone))
    return LAST_HOUR <= (t.hour, t.minute) <= SESSION_CLOSE


def stoch_extreme(k: float, direction: str) -> bool:
    """Oversold for a long, overbought for a short; NaN is neither."""
    return k < STOCH_OVERSOLD if direction == "up" else k > STOCH_OVERBOUGHT


def ema_signal(candles: Sequence[Candle], ema: Sequence[float], i: int) -> Optional[str]:
    """Model 1 on candle i: "up" if candle i-1 came down onto the EMA (the candle before
    it was clear above) and candle i is the entry over i-1's body
    (trade_plan.is_entry_candle); "down" the mirror."""
    if i < EMA_PERIOD + 1:
        return None
    before, touch, c = candles[i - 2], candles[i - 1], candles[i]
    if not touch.low <= ema[i - 1] <= touch.high:
        return None
    if before.low > ema[i - 2] and is_entry_candle(touch, c, "up"):
        return "up"
    if before.high < ema[i - 2] and is_entry_candle(touch, c, "down"):
        return "down"
    return None


def recent_touches(zone: Zone, last_idx: int, max_gap: int = MAX_GAP_BARS) -> list[int]:
    """The zone's touches leading up to candle last_idx, each within max_gap candles of
    the next; the older ones before a longer gap are dropped."""
    chain: list[int] = []
    for k in reversed(zone.touch_idx):
        if (chain[-1] if chain else last_idx) - k > max_gap:
            break
        chain.append(k)
    return chain[::-1]


def zone_signal(
    window: Sequence[Candle], max_gap: int = MAX_GAP_BARS, range_distance: float = RANGE_DISTANCE
) -> Optional[tuple[str, Zone, int]]:
    """Model 2 on the window's last candle: the entry (trade_plan.zone_entry) at a zone
    with MIN_TOUCHES - 1 recent touches or more before this visit — the visit is the
    third. From above → "up". The zone comes back with those touches only, and the
    visit's first candle. Ranging = another zone ahead touched as often lately — price
    bouncing between the two; a zone price last turned at hours ago doesn't count."""
    zones = detect_zones(window, min_touches=MIN_TOUCHES - 1)
    recent = [replace(z, touch_idx=recent_touches(z, len(window) - 1, max_gap)) for z in zones]
    for zone in zones:  # strongest first
        found = zone_entry(window, zone.bottom, zone.top, ZONE_REACH * zone.unit)
        if not found:
            continue
        direction, a = found
        touches = recent_touches(replace(zone, touch_idx=[k for k in zone.touch_idx if k < a]), a, max_gap)
        if len(touches) < MIN_TOUCHES - 1:
            continue
        zone = replace(zone, start_idx=touches[0], touch_idx=touches)
        if is_ranging(window, recent, zone, direction, range_distance):
            return None
        return direction, zone, a
    return None


def _zone_scalp(model: int, window: Sequence[Candle], candles: Sequence[Candle], i: int, found) -> ScalpSignal:
    """The ScalpSignal of model 2 or 3 for zone_signal's find on the window ending at candle i."""
    direction, zone, a = found
    last = len(window) - 1
    return ScalpSignal(
        model=model, direction=direction, time=_iso(candles[i].time),
        # The signal candle gets the arrow; a touch's dot there would cover it.
        touch_times=[_iso(window[k].time) for k in [*zone.touch_idx, *([a] if a < last else [])]],
        zone_bottom=round(zone.bottom, 2), zone_top=round(zone.top, 2),
        candles=list(candles[: i + 1]),
        plan=zone_plan(window, a, direction, zone.bottom, zone.top, zone.unit),
    )


def signals_at(candles: Sequence[Candle], ema: Sequence[float], stoch: Sequence[float], i: int) -> list[ScalpSignal]:
    """Both models' signals on candle i, seeing only the candles up to it."""
    if in_last_hour(candles[i].time + BAR_SECONDS):
        return []
    out = []
    lo = max(0, i + 1 - ZONE_WINDOW_BARS)
    window = candles[lo : i + 1]
    direction = ema_signal(candles, ema, i)
    if direction:
        # The stop behind the candle on the line (and the signal candle, if it reached further).
        unit = typical_candle(window) * candles[i].close
        stop = behind(candles[i - 1 : i + 1], direction, ema[i - 1], unit)
        out.append(ScalpSignal(
            model=1, direction=direction, time=_iso(candles[i].time), touch_times=[_iso(candles[i - 1].time)],
            candles=list(candles[: i + 1]), ema=list(ema[: i + 1]),
            plan=plan(direction, candles[i].close, stop),
        ))
    found = zone_signal(window)
    if found and stoch_extreme(stoch[i], found[0]):
        out.append(_zone_scalp(2, window, candles, i, found))
    return out


def m1_signal_at(candles: Sequence[Candle], i: int) -> Optional[ScalpSignal]:
    """Model 3 on 1m candle i, seeing only its day's candles up to it."""
    opened = datetime.fromtimestamp(candles[i].time, ZoneInfo(settings.scan_timezone))
    if (opened.hour, opened.minute) < SESSION_OPEN or in_last_hour(candles[i].time + M1_BAR_SECONDS):
        return None
    lo = i
    while lo > max(0, i + 1 - M1_WINDOW_BARS) and candles[lo].time - candles[lo - 1].time < SESSION_GAP_SECONDS:
        lo -= 1
    window = candles[lo : i + 1]
    found = zone_signal(window, M1_MAX_GAP_BARS, M1_RANGE_DISTANCE)
    return _zone_scalp(3, window, candles, i, found) if found else None


def fetch_candles(
    period: str = LIVE_PERIOD, interval: str = TIMEFRAME, bar_seconds: int = BAR_SECONDS,
    symbol: str = SYMBOL, prepost: bool = False,
) -> list[Candle]:
    """The symbol's closed candles (the index's by default) — Yahoo includes the one still
    in progress."""
    try:
        df = yf.Ticker(symbol).history(interval=interval, period=period, prepost=prepost)
    except Exception:
        logger.warning("Nasdaq scalp: %s %s history fetch failed", symbol, interval)
        return []
    if df is None or df.empty:
        return []
    candles = _to_candles(df)
    if candles and candles[-1].time + bar_seconds > time.time():
        candles.pop()
    return candles


def with_premarket(session: Sequence[Candle], etf: Sequence[Candle]) -> list[Candle]:
    """The index's 1m candles with each day's last M1_WINDOW_BARS minutes before the open
    put in front — the ETF's candles times that day's index/ETF ratio (the median over
    the minutes both have). A day the ETF has no session minutes for is left as is."""
    etf_close = {c.time: c.close for c in etf}
    days: list[list[Candle]] = []
    for c in session:
        if not days or c.time - days[-1][-1].time >= SESSION_GAP_SECONDS:
            days.append([])
        days[-1].append(c)
    out: list[Candle] = []
    for day in days:
        ratios = [c.close / etf_close[c.time] for c in day if etf_close.get(c.time)]
        if ratios:
            r = statistics.median(ratios)
            start = day[0].time - M1_WINDOW_BARS * M1_BAR_SECONDS
            out += [
                Candle(time=c.time, open=round(c.open * r, 2), high=round(c.high * r, 2),
                       low=round(c.low * r, 2), close=round(c.close * r, 2))
                for c in etf
                if start <= c.time < day[0].time
            ]
        out += day
    return out


def fetch_m1_candles(period: str = M1_LIVE_PERIOD) -> list[Candle]:
    session = fetch_candles(period, M1_TIMEFRAME, M1_BAR_SECONDS)
    if not session:
        return []
    etf = fetch_candles(period, M1_TIMEFRAME, M1_BAR_SECONDS, symbol=PREMARKET_SYMBOL, prepost=True)
    return with_premarket(session, etf)


def history_signals(candles: Sequence[Candle]) -> list[ScalpSignal]:
    """Every signal the live run would have sent on these candles, oldest first."""
    ema, stoch = ema_series(candles), stoch_series(candles)
    return _with_rest_of_day(candles, [s for i in range(len(candles)) for s in signals_at(candles, ema, stoch, i)])


def history_m1_signals(candles: Sequence[Candle]) -> list[ScalpSignal]:
    return _with_rest_of_day(candles, [s for i in range(len(candles)) if (s := m1_signal_at(candles, i))])


def _with_rest_of_day(candles: Sequence[Candle], found: list[ScalpSignal]) -> list[ScalpSignal]:
    for s in found:
        s.after = alert_chart.rest_of_day(candles, len(s.candles) - 1)
    return found


def format_message(s: ScalpSignal, example: bool = False) -> str:
    head = "🟢 לונג" if s.direction == "up" else "🔴 שורט"
    text = f"{head} · <b>{NAME}</b> · מודל {s.model} · {s.timeframe}\n{telegram.link(TRADINGVIEW_URL, 'TradingView')}"
    if example:
        when = datetime.fromisoformat(s.time) + timedelta(seconds=s.bar_seconds)
        text += f"\n🕘 דוגמה מההיסטוריה · {when.astimezone(ZoneInfo(settings.alert_timezone)):%d/%m %H:%M}"
    return text


def send_alert(s: ScalpSignal, example: bool = False) -> bool:
    """The chart picture with the message as its caption — or the message alone if the
    picture can't be drawn. From the Nasdaq bot; the trade is watched from then on
    (trade_tracker.py), unless it's an example from the past."""
    text = format_message(s, example)
    title = f"{NAME} · {s.timeframe} · Model {s.model}"
    try:
        full = [*s.candles, *s.after]
        k, d = _stoch_rsi(pd.Series([c.close for c in full]))
        # The EMA50 on every model's chart (the user's ask, 1/11/26), the Stochastic RSI
        # under the candles; the trade's boxes without their labels.
        shown = dict(
            line=ema_series(full), legend=EMA_LEGEND, stoch=(k.tolist(), d.tolist()), plan_labels=False, after=s.after,
        )
        if s.model == 1:
            png = alert_chart.render(
                s.candles, title, None, None, None, direction=s.direction, breakout=s.time,
                touches=s.touch_times, plan=s.plan, **shown,
            )
        else:
            png = alert_chart.render(
                s.candles, title, s.zone_bottom, s.zone_top, s.touch_times[0], direction=s.direction,
                breakout=s.time, touches=s.touch_times, plan=s.plan, **shown,
            )
    except Exception:
        logger.exception("Nasdaq scalp: chart picture failed, sending text only")
        png = None
    return trade_tracker.send_alert(png, text, "nasdaq", s.key, SYMBOL, NAME, None if example else s.plan)


def run_nasdaq_scalp(db: Session) -> int:
    """Checks the 5m candles that just closed and sends each new signal; returns how many
    went out."""
    with _run_lock:
        candles = fetch_candles()
        if len(candles) < EMA_PERIOD + 2:
            return 0
        ema, stoch = ema_series(candles), stoch_series(candles)
        return _send_new(db, [
            s
            for i in range(len(candles) - 2, len(candles))
            if time.time() - (candles[i].time + BAR_SECONDS) <= MAX_AGE_SECONDS
            for s in signals_at(candles, ema, stoch, i)
            if s.model in LIVE_MODELS
        ])


def run_nasdaq_scalp_m1(db: Session) -> int:
    """Model 3: the same on the 1m candles that just closed."""
    with _run_lock:
        candles = fetch_m1_candles()
        return _send_new(db, [
            s
            for i in range(max(0, len(candles) - 2), len(candles))
            if time.time() - (candles[i].time + M1_BAR_SECONDS) <= 2 * M1_BAR_SECONDS
            if (s := m1_signal_at(candles, i))
        ])


def _send_new(db: Session, fresh: list[ScalpSignal]) -> int:
    """Sends the signals not sent before. One whose send fails isn't recorded, so the next
    run tries it again."""
    already = {k for (k,) in db.query(SentAlert.key).filter(SentAlert.key.in_([s.key for s in fresh]))}
    sent = 0
    for s in fresh:
        if s.key in already:
            continue
        if sent:
            time.sleep(SEND_INTERVAL_SECONDS)
        if send_alert(s):
            db.add(SentAlert(key=s.key, symbol=SYMBOL))
            db.commit()
            sent += 1
    if sent:
        logger.info("Nasdaq scalp: %d alerts sent", sent)
    return sent


if __name__ == "__main__":
    per_model = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    found = history_signals(fetch_candles(HISTORY_PERIOD)) + history_m1_signals(fetch_m1_candles(M1_HISTORY_PERIOD))
    for model in (1, 2, 3):
        mine = [s for s in found if s.model == model]
        print(f"Model {model}: {len(mine)} signals")
        for s in mine[-per_model:]:
            print(" ", s.time, s.direction, "sent" if send_alert(s, example=True) else "FAILED")
            time.sleep(SEND_INTERVAL_SECONDS)
