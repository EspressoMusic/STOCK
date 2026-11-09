"""The 1h bot's one strategy since 4/11/26 (the user's ask), in place of its forex alerts
("פורקס המשך", touch3_15m.py, off, code kept): stocks
day trading, as signals for the user to check. No entry, stop or target for now, the
user's call — the bot opens no trade and follows none.

A short, read top down; a long mirrors it:

- Daily: a tight zone of swing highs (touch3_15m.side_zones, at most MAX_HEIGHT high) with
  MIN_TOUCHES touches or more in the last WINDOW_BARS days, and price rejected from it: a
  candle of the last REJECT_BARS days wicked into it and closed red under it, every close
  since under it too.
- 4h: the last closed candle is red.
- 1h and 15m: the last closed candle closes under its 50 EMA, and the 15m under the zone.
- Stronger, if the 15m also shows MIN_WICKS or more wicks rejected at the EMA (rejections):
  the alert says so, the others go out too.

The stocks: the ~100 big names and every US one of a good size and liquidity (universe).
The daily part is read once a day (watchlist), so only the day's few stocks are looked at
every 15 minutes, right after each 15m candle of the session closes (scheduler.py), with
none from the session's last hour. A stock alerts once a day per side, and once more if
it turns strong later that day. `python -m app.stock_day N` sends the last N signals of
the past 60 days of each kind, strong and not, marked as examples.

On the past 60 days (today's ~660 stocks, no costs): ~27 signals a day, ~3 of them strong,
nearly half on the session's first candle. From the signal's close to the day's close 50%
went the signal's way, -0.02% on average; the strong ones 57%, +0.19% (160 signals; 54% with
the rejection on yesterday's candle alone) — so far a list to check by eye, not an edge."""
import logging
import sys
import threading
import time
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from sqlalchemy.orm import Session
from yfinance import EquityQuery

from . import alert_chart, telegram, trade_tracker
from .candles import Candle, _to_candles, group_4h
from .config import settings
from .misc_strategies import LARGE_CAPS
from .models import SentAlert
from .nasdaq_scalp import ema_series
from .scanner import NASDAQ_EXCHANGES, _fetch_all_quotes, _is_derivative
from .touch3_15m import STRAY, _clear, side_zones
from .zone_touch_scan import send_new
from .zones import Zone

logger = logging.getLogger(__name__)

BOT = "1h"  # telegram.bot_chat — the bot that was "פורקס המשך"
TAG = "מסחר יומי"
NY = ZoneInfo("America/New_York")
BAR_SECONDS = 900
HOUR = 3600
# Zones from the last three months' daily candles, so the touches are recent.
WINDOW_BARS = 60
MIN_TOUCHES = 3
# Tight: at most this many typical daily candles high.
MAX_HEIGHT = 0.75
# The rejection: at most this many daily candles back, so it's fresh for a day trade.
REJECT_BARS = 2
# The 15m wicks at the EMA that make a signal stronger, in the run of candles under it, at
# most a session back.
MIN_WICKS = 2
WICK_BARS = 26
# Good stocks: big, liquid and not cheap (~700 of them on 4/11/26).
UNIVERSE_MIN_CAP = 10_000_000_000
UNIVERSE_MIN_VOLUME = 1_000_000
UNIVERSE_MIN_PRICE = 10
UNIVERSE_CAP = 1500
EXCHANGES = [*NASDAQ_EXCHANGES, "NYQ"]
DAILY_PERIOD = "1y"
M15_PERIOD = "10d"  # enough candles for the EMA to settle
H1_PERIOD = "3mo"
SESSION_OPEN = (9, 30)
LAST_HOUR = (15, 0)  # no signal from a candle closing from here on
# A signal is sent only while its candle closed at most this long ago.
MAX_AGE_SECONDS = 2 * BAR_SECONDS
MAX_PER_RUN = 15
SEND_INTERVAL_SECONDS = 1.1
DAILY_BARS_SHOWN = 60
H4_BARS_SHOWN = 60
M15_BARS_SHOWN = 60
PICTURE_SIZE = (21, 7)  # three charts in a row
M15_TIME_LABELS = 6  # its labels are long: more would run together at this width


@dataclass
class DailySetup:
    direction: str  # "down" = rejected down from swing highs (short) | "up" = from swing lows (long)
    zone: Zone
    touches: list[int]  # in the daily candles, the last one the rejection
    daily: list[Candle] = field(default_factory=list, repr=False)  # closed, up to the day before


@dataclass
class Intraday:
    """One stock's candles under the day, with what the rules read off them, all aligned."""
    m15: list[Candle]
    ema15: list[float]
    h1: list[Candle]
    ema1h: list[float]
    h1_times: list[int]
    h4: list[Candle]
    h4_times: list[int]


@dataclass
class Signal:
    symbol: str
    setup: DailySetup
    i: int  # the signal's 15m candle
    wicks: list[int]  # the 15m candles rejected at the EMA, oldest first
    m15: list[Candle] = field(default_factory=list, repr=False)  # up to the signal's candle
    ema: list[float] = field(default_factory=list, repr=False)  # the 15m EMA, may run on over after
    after: list[Candle] = field(default_factory=list, repr=False)  # an example's: the rest of its day
    h4: list[Candle] = field(default_factory=list, repr=False)  # up to the last closed one, the one the rule read
    h4_after: list[Candle] = field(default_factory=list, repr=False)  # an example's: up to the end of its day

    @property
    def direction(self) -> str:
        return self.setup.direction

    @property
    def strong(self) -> bool:
        return len(self.wicks) >= MIN_WICKS

    @property
    def time(self) -> int:
        return self.m15[self.i].time

    def _key(self, strong: bool) -> str:
        day = datetime.fromtimestamp(self.time, NY).strftime("%Y-%m-%d")
        return f"stock-day:{self.symbol}:{self.direction}:{day}{':strong' if strong else ''}"

    @property
    def key(self) -> str:
        return self._key(self.strong)

    @property
    def strong_key(self) -> str:
        return self._key(True)


_run_lock = threading.Lock()
_watchlist: tuple[str, dict[str, list[DailySetup]]] = ("", {})


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _rejected(window: Sequence[Candle], zone: Zone, direction: str) -> Optional[list[int]]:
    """The zone's touches, the last one the rejection: the last candle in the last
    REJECT_BARS that wicked into it, of the trade's colour, with its close and every close
    since back out."""
    down = direction == "down"
    if zone.top - zone.bottom > MAX_HEIGHT * zone.unit:
        return None
    last = len(window) - 1
    r = next(
        (k for k in range(last, max(-1, last - REJECT_BARS), -1)
         if (window[k].high >= zone.bottom if down else window[k].low <= zone.top)),
        None,
    )
    if r is None or any(c.close >= zone.bottom if down else c.close <= zone.top for c in window[r:]):
        return None
    if not (window[r].close < window[r].open if down else window[r].close > window[r].open):
        return None
    prior = [k for k in zone.touch_idx if k < r]
    if prior and not _clear(window, zone, direction, prior[-1], r):
        prior.pop()  # the same visit
    touches = [*prior, r]
    if len(touches) < MIN_TOUCHES:
        return None
    # The zone held: no close far through it since the first touch.
    stray = STRAY * zone.unit
    if any(c.close > zone.top + stray if down else c.close < zone.bottom - stray for c in window[touches[0] : r]):
        return None
    return touches


def daily_setups(daily: Sequence[Candle]) -> list[DailySetup]:
    """The zones these closed daily candles were last rejected from: the most touched one
    each way, if any."""
    window = list(daily[-WINDOW_BARS:])
    found: dict[str, DailySetup] = {}
    for direction, zone in side_zones(window):
        if direction not in found and (touches := _rejected(window, zone, direction)):
            found[direction] = DailySetup(direction, zone, touches, window)
    return list(found.values())


def rejections(m15: Sequence[Candle], ema: Sequence[float], i: int, direction: str) -> list[int]:
    """The wicks rejected at the EMA in the run of candles up to i closing on the trade's
    side of it: each poked through the line with its whole body on that side, one per visit
    to the line. Oldest first."""
    down = direction == "down"
    out: list[int] = []
    touching = False
    for k in range(i, max(-1, i - WICK_BARS), -1):
        c = m15[k]
        if c.close >= ema[k] if down else c.close <= ema[k]:
            break
        poked = c.high >= ema[k] if down else c.low <= ema[k]
        body_out = max(c.open, c.close) < ema[k] if down else min(c.open, c.close) > ema[k]
        if poked and body_out and not touching:
            out.append(k)
        touching = poked
    return out[::-1]


def _last_closed(times: Sequence[int], now: int, seconds: int) -> Optional[int]:
    """The last candle of this length, starting at these times, closed by now."""
    k = bisect_right(times, now - seconds) - 1
    return k if k >= 0 else None


def intraday(m15: list[Candle], h1_df: pd.DataFrame) -> Intraday:
    h1 = _to_candles(h1_df)
    h4 = _to_candles(group_4h(h1_df))
    return Intraday(m15, ema_series(m15), h1, ema_series(h1), [b.time for b in h1], h4, [b.time for b in h4])


def signal_at(symbol: str, setup: DailySetup, data: Intraday, i: int) -> Optional[Signal]:
    """The signal when 15m candle i has just closed, seeing only what had closed by then."""
    down = setup.direction == "down"
    c = data.m15[i]
    if not (c.close < setup.zone.bottom if down else c.close > setup.zone.top):
        return None
    if not (c.close < data.ema15[i] if down else c.close > data.ema15[i]):
        return None
    now = c.time + BAR_SECONDS
    one = _last_closed(data.h1_times, now, HOUR)
    if one is None or not (data.h1[one].close < data.ema1h[one] if down else data.h1[one].close > data.ema1h[one]):
        return None
    four = _last_closed(data.h4_times, now, 4 * HOUR)
    if four is None:
        return None
    h4 = data.h4[four]
    if not (h4.close < h4.open if down else h4.close > h4.open):
        return None
    wicks = rejections(data.m15, data.ema15, i, setup.direction)
    return Signal(symbol, setup, i, wicks, data.m15[: i + 1], data.ema15[: i + 1], h4=data.h4[: four + 1])


def _is_today_or_later(c: Candle, day: str) -> bool:
    # A daily candle's time is that day's midnight, New York's or UTC's: read at midday.
    return datetime.fromtimestamp(c.time + 43200, timezone.utc).strftime("%Y-%m-%d") >= day


def universe() -> list[str]:
    """The big names, and every US stock of a good size and liquidity."""
    query = EquityQuery("and", [
        EquityQuery("eq", ["region", "us"]),
        EquityQuery("is-in", ["exchange", *EXCHANGES]),
        EquityQuery("gt", ["dayvolume", UNIVERSE_MIN_VOLUME]),
        EquityQuery("gt", ["intradaymarketcap", UNIVERSE_MIN_CAP]),
        EquityQuery("gt", ["intradayprice", UNIVERSE_MIN_PRICE]),
    ])
    try:
        quotes = _fetch_all_quotes(query, sort_field="dayvolume", sort_asc=False, fetch_cap=UNIVERSE_CAP)
    except Exception:
        logger.exception("Stock day trading: the screener failed — the big names only")
        quotes = []
    found = [q["symbol"] for q in quotes if q.get("symbol") and not _is_derivative(q["symbol"], q.get("shortName"))]
    return list(dict.fromkeys([*LARGE_CAPS, *found]))


def download(symbols: Sequence[str], interval: str, period: str) -> dict[str, pd.DataFrame]:
    """Each stock's bars from one download; none on a failure."""
    if not symbols:
        return {}
    try:
        df = yf.download(
            list(symbols), period=period, interval=interval, group_by="ticker", threads=True,
            progress=False, prepost=False, auto_adjust=False,
        )
    except Exception:
        logger.warning("Stock day trading: the %s candles download failed", interval)
        return {}
    if df is None or df.empty:
        return {}
    out = {}
    for symbol in symbols:
        try:
            sub = df[symbol] if isinstance(df.columns, pd.MultiIndex) else df
        except KeyError:
            continue
        sub = sub.dropna(how="all")
        if not sub.empty:
            out[symbol] = sub
    return out


def watchlist(now: float) -> dict[str, list[DailySetup]]:
    """The day's stocks, with the daily zones they were rejected from — built once a day,
    on the days closed before it."""
    global _watchlist
    day = datetime.fromtimestamp(now, NY).strftime("%Y-%m-%d")
    if _watchlist[0] == day:
        return _watchlist[1]
    symbols = universe()
    out = {}
    frames = download(symbols, "1d", DAILY_PERIOD)
    for symbol, df in frames.items():
        daily = [c for c in _to_candles(df) if not _is_today_or_later(c, day)]
        if setups := daily_setups(daily):
            out[symbol] = setups
    logger.info("Stock day trading: %d of %d stocks rejected from a daily zone", len(out), len(symbols))
    if len(frames) > len(LARGE_CAPS):  # else the screener failed: tried again next time
        _watchlist = (day, out)
    return out


def scan(now: float) -> list[Signal]:
    """The signals on the 15m candle that just closed, strong ones first."""
    setups = watchlist(now)
    symbols = list(setups)
    m15s, h1s = download(symbols, "15m", M15_PERIOD), download(symbols, "1h", H1_PERIOD)
    found = []
    for symbol in symbols:
        if symbol not in m15s or symbol not in h1s:
            continue
        m15 = _to_candles(m15s[symbol])
        if m15 and m15[-1].time + BAR_SECONDS > now:  # Yahoo includes the one in progress
            m15.pop()
        if not m15 or now - (m15[-1].time + BAR_SECONDS) > MAX_AGE_SECONDS:
            continue
        data = intraday(m15, h1s[symbol])
        for setup in setups[symbol]:
            if s := signal_at(symbol, setup, data, len(m15) - 1):
                found.append(s)
    return sorted(found, key=lambda s: not s.strong)


def _signal_hours(close: float) -> bool:
    """Whether a 15m candle closing at this time can signal."""
    t = datetime.fromtimestamp(close, NY)
    return SESSION_OPEN < (t.hour, t.minute) < LAST_HOUR


def history_signals(symbol: str, daily: Sequence[Candle], h1_df: pd.DataFrame, m15: list[Candle]) -> list[Signal]:
    """Every signal the live run would have sent on these candles, oldest first."""
    data = intraday(m15, h1_df)
    out = []
    sent: set[str] = set()
    setups: list[DailySetup] = []
    today = ""
    for i, c in enumerate(m15):
        day = datetime.fromtimestamp(c.time, NY).strftime("%Y-%m-%d")
        if day != today:
            today = day
            setups = daily_setups([d for d in daily if not _is_today_or_later(d, day)])
        if not setups or not _signal_hours(c.time + BAR_SECONDS):
            continue
        for setup in setups:
            s = signal_at(symbol, setup, data, i)
            if s is None or s.key in sent or s.strong_key in sent:
                continue
            sent.add(s.key)
            s.after = alert_chart.rest_of_day(m15, i)
            s.ema = data.ema15[: i + 1 + len(s.after)]
            end = (s.after or s.m15)[-1].time
            s.h4_after = [c for c in data.h4[len(s.h4) :] if c.time <= end]
            out.append(s)
    return out


def format_message(s: Signal, example: bool = False) -> str:
    """The stock, then every reason it was taken — so the user can check each one."""
    long = s.direction == "up"
    tag = f"{TAG} · 💪 חזק" if s.strong else TAG
    lines = [telegram.alert_caption(s.symbol, None, long, tag)]
    if example:
        when = datetime.fromtimestamp(s.time + BAR_SECONDS, ZoneInfo(settings.alert_timezone))
        lines.append(f"🕘 דוגמה מההיסטוריה · {when:%d/%m %H:%M}")
    setup = s.setup
    rejected = datetime.fromtimestamp(setup.daily[setup.touches[-1]].time + 43200, timezone.utc)
    side = "מעל " if long else "מתחת ל-"
    lines += [
        f"📅 יומי: אזור {telegram.price(round(setup.zone.bottom, 2))}-{telegram.price(round(setup.zone.top, 2))}, "
        f"{len(setup.touches)} נגיעות, נדחה ב-{rejected:%d/%m}",
        f"🕓 4h: נר אחרון {'ירוק' if long else 'אדום'}",
        f"📈 1h ו-15m: {side}EMA 50",
    ]
    if s.strong:
        lines.append(f"💪 15m: {len(s.wicks)} דחיות בפתיל על ה-EMA 50")
    return "\n".join(lines)


def picture(s: Signal) -> bytes:
    """Top down, left to right: the daily zone with its touches, the 4h with the closed
    candle the rule read, the 15m with its EMA and its wicks, each numbered at its tip."""
    setup = s.setup
    tip = "high" if s.direction == "down" else "low"
    touches = [_iso(setup.daily[k].time) for k in setup.touches]
    return alert_chart.render_side_by_side(
        ((setup.daily, f"{s.symbol} · 1D", setup.zone.bottom, setup.zone.top, touches[0]), dict(
            direction=s.direction, breakout=touches[-1], touches=touches[:-1], time_format="%d/%m",
            zone_colors=alert_chart.GOLD, min_bars=DAILY_BARS_SHOWN,
        )),
        ((s.h4, f"{s.symbol} · 4h", None, None, None), dict(
            direction=s.direction, breakout=_iso(s.h4[-1].time), time_format="%d/%m", after=s.h4_after,
            min_bars=H4_BARS_SHOWN,
        )),
        ((s.m15, f"{s.symbol} · 15m", None, None, None), dict(
            direction=s.direction, breakout=_iso(s.time),
            numbers=[(_iso(s.m15[k].time), getattr(s.m15[k], tip)) for k in s.wicks],
            line=s.ema, legend=(("EMA 50", alert_chart.LINE),), after=s.after, min_bars=M15_BARS_SHOWN,
            time_labels=M15_TIME_LABELS,
        )),
        size=PICTURE_SIZE,
    )


def send_alert(s: Signal, example: bool = False) -> bool:
    """The chart picture with the caption, or the caption alone if it can't be drawn."""
    try:
        png = picture(s)
    except Exception:
        logger.exception("Stock day trading: chart picture failed for %s, sending text only", s.key)
        png = None
    return trade_tracker.send_alert(png, format_message(s, example), BOT, s.key, s.symbol, None, None)


def run_stock_day(db: Session) -> int:
    """Checks the 15m candle that just closed and sends each new signal; returns how many
    went out. Before the open it only reads the daily part; after the hours a candle can
    signal in, nothing is fetched."""
    now = time.time()
    clock = datetime.fromtimestamp(now, NY)
    with _run_lock:
        if (clock.hour, clock.minute) < SESSION_OPEN:
            watchlist(now)  # takes minutes: ready before the first candle closes
            return 0
        if not SESSION_OPEN < (clock.hour, clock.minute) <= LAST_HOUR:
            return 0
        found = [s for s in scan(now) if _signal_hours(s.time + BAR_SECONDS)]
        # Strong already sent today: nothing more on this stock and side.
        strong = {k for (k,) in db.query(SentAlert.key).filter(SentAlert.key.in_([s.strong_key for s in found]))}
        found = [s for s in found if s.strong_key not in strong]
        sent = send_new(db, found, send_alert, MAX_PER_RUN)
        logger.info("Stock day trading: %d signals, %d sent", len(found), sent)
        return sent


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    dailies = download(LARGE_CAPS, "1d", DAILY_PERIOD)
    m15s, h1s = download(LARGE_CAPS, "15m", "60d"), download(LARGE_CAPS, "1h", "6mo")
    found = []
    for symbol in LARGE_CAPS:
        if symbol in dailies and symbol in m15s and symbol in h1s:
            found += history_signals(symbol, _to_candles(dailies[symbol]), h1s[symbol], _to_candles(m15s[symbol]))
    print(f"{len(found)} signals in the past 60 days ({sum(s.strong for s in found)} strong)")
    found.sort(key=lambda s: s.time)
    for s in [*[s for s in found if s.strong][-count:], *[s for s in found if not s.strong][-count:]]:
        print(" ", s.symbol, _iso(s.time), s.direction, "strong" if s.strong else "", "sent" if send_alert(s, example=True) else "FAILED")
        time.sleep(SEND_INTERVAL_SECONDS)
