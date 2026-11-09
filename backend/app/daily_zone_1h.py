"""The forex bot (the main bot), as the user changed it on 5/11/26, in place of
daily_zone_30m.py's 50 EMA on 30m (code kept):

1. Daily: price comes within REACH of a zone it turned at three times or more. From below it's
   resistance, a short; from above, support, a long. The zones come from forex days
   (daily_candle.py), a touch being any swing with one candle on each side (my default: with
   daily_zones' three, the zone of the user's EUR/USD example isn't there).
2. 4h: a closed candle in the trade's colour since price came to the zone, any one (the user's
   pick, 5/11/26).
3. 1h, for a short (a long is the mirror), as the user's pictures of 5/11/26 and 8/11/26 mark it:
   - the structure is the last run of green candles to set a new high in bodies since the visit
     began, a green run under the high being only a pullback (my reading of the pictures);
   - the break: a red candle closes under where that run started, a wick alone doesn't count;
   - the return: a candle closes back at the top of that run's bodies;
   - the alert: after the return, a red 1h candle closes, and during it a 15m candle closed
     under the 50 EMA on 15m from at or over it (the user, 9/11/26, in place of the structure
     breaking again, 8/11/26), within MAX_WAIT candles of the break. Both are at the zone, not
     far from it (the user's ask): the run broken started within REACH of the zone, and the
     alert's candle reaches it; gone from it, another return is waited for. Once a visit; a
     close through the zone ends it.

A signal only: no entry, stop or target, no trade watched (the bots' rule since 4/11/26). The
daily candle (daily_candle.py) holds nothing back; when it agrees, the alert says it's a strong
one (the user's ask, 1/11/26).

The picture is the daily, 4h and 1h charts side by side, thick black lines between them. The
daily one, narrower, shows why the zone is strong (the user, 8/11/26: a push from a place, then
price back at it): its turns and the alert's day, the return, circled. On the 1h one, a box around
the break's candle and the alert's (the user, 8/11/26: no 1 and 2, no arrow). Runs every hour,
on the 1h candle closes (scheduler.py), and on demand; every visit is sent once, remembered in the sent_alerts
table. `python -m app.daily_zone_1h N` sends the last N setups of the past month, marked as
examples.

With the second break (8/11/26), on 127 weeks, 20 pairs, before the spread: ~6 signals a week;
with a stop behind the zone and the wicks from the return on, at 1:3 24% reached the target,
-0.05R a signal; at 1:2 33%, 0R. Yahoo keeps 15m bars for 60 days only, so the 15m EMA can't be
tested further back."""
import bisect
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from sqlalchemy.orm import Session

from . import alert_chart, telegram, trade_tracker
from .candles import Candle, _to_candles, group_4h
from .config import settings
from .daily_candle import confirms, daily_candles
from .daily_zone_30m import day_so_far
from .daily_zone_4h import BOT, DAILY_MIN_TOUCHES, DAILY_WINDOW, STRONG_TAG, UNIVERSE
from .nasdaq_scalp import ema_series
from .technical_scan import _round_sig
from .zone_touch_scan import send_new
from .zones import Zone, detect_zones, typical_candle

logger = logging.getLogger(__name__)

TIMEFRAME = "1h"
BAR_SECONDS = 3600
EMA_TIMEFRAME = "15m"
EMA_BAR_SECONDS = 900
EMA_PERIOD = "60d"  # as far back as Yahoo keeps 15m bars
EMA_LENGTH = 50
# Enough 15m candles for the EMA to settle.
EMA_SETTLE = 150
BAR_4H_SECONDS = 4 * BAR_SECONDS
DAY = 86400
ALL_DAYS = 800  # more than the period
PERIOD = "1y"  # the daily zones, the typical candles, the charts
EXAMPLE_DAYS = 30
FETCH_TRIES = 3
FETCH_PAUSE_SECONDS = 2
# A swing with this many candles on each side is a touch of a daily zone.
PIVOT_BARS = 1
# A candle this near the zone (in typical 1h candles) came to it.
REACH = 0.5
TYPICAL_BARS = 200
# A visit that began this many candles before the day still counts, on the day's zones.
VISIT_BARS = 96
# The alert comes within this many candles of the break (my default: two days).
MAX_WAIT = 48
# Candles shown on each chart, up to the alert: zoomed in (the user, 8/11/26), four days of 4h, a
# day and a bit of 1h — more when the break is further back, with BREAK_LEAD before it. The
# daily one shows the zone's turns, at least DAILY_SHOWN days.
SHOWN_4H = 24
SHOWN_1H = 30
BREAK_LEAD = 5
DAILY_SHOWN = 40
PICTURE_SIZE = (19, 6)
WIDTHS = (0.8, 1, 1)  # daily, 4h, 1h
SEND_INTERVAL_SECONDS = 1.1

OPPOSITE = {"down": "up", "up": "down"}


@dataclass
class Found:
    zone: Zone
    direction: str  # "down" = short | "up" = long
    visit: int  # the candle price came to the zone on
    broke: int  # the break
    back: int  # the return
    entry: int  # the alert's: a 1h candle the trade's way, the 15m crossing the EMA during it
    turns: list[int] = field(default_factory=list)  # the daily candles (unix) price turned at the zone on


@dataclass
class ZoneSetup:
    symbol: str
    name: Optional[str]
    direction: str
    zone_bottom: float
    zone_top: float
    touch_count: int  # the daily zone's
    time: str  # the alert candle's; the alert goes out when it closes
    key: str  # one alert per visit
    candles_4h: list[Candle] = field(default_factory=list, repr=False)  # up to the alert, the last one forming
    candles_1h: list[Candle] = field(default_factory=list, repr=False)  # up to the alert
    strong: bool = False  # the daily candle agrees (daily_candle.confirms)
    after: list[Candle] = field(default_factory=list, repr=False)  # an example's: the rest of its day
    broke: str = ""  # the break's candle
    candles_1d: list[Candle] = field(default_factory=list, repr=False)  # closed days, then the alert's so far
    turns: list[str] = field(default_factory=list)  # the daily candles price turned at the zone on


@dataclass
class _Structure:
    peak: Optional[float] = None  # the furthest body so far, towards the zone
    level: Optional[float] = None  # where the run that last set it started
    top: Optional[float] = None  # how far that run's bodies went


@dataclass
class _Visit:
    direction: str
    start: int
    first: _Structure = field(default_factory=_Structure)  # until the break
    broke: Optional[int] = None
    back: Optional[int] = None


_run_lock = threading.Lock()


def strong_zones(days: Sequence[Candle]) -> list[Zone]:
    return detect_zones(days[-DAILY_WINDOW:], min_touches=DAILY_MIN_TOUCHES, pivot_bars=PIVOT_BARS)


def _in_colour(c: Candle, direction: str) -> bool:
    return c.close < c.open if direction == "down" else c.close > c.open


def _past(price: float, level: float, direction: str) -> bool:
    """price is past level the trade's way: under it for a short."""
    return price < level if direction == "down" else price > level


def _note(s: _Structure, direction: str, m: Sequence[Candle], k: int) -> None:
    """Candle k, not a break: the structure, if its body went further than any."""
    c = m[k]
    edge = max(c.open, c.close) if direction == "down" else min(c.open, c.close)
    if s.peak is not None and not _past(s.peak, edge, direction):
        return
    s.peak = edge
    opposite = OPPOSITE[direction]
    if _in_colour(c, opposite):
        i = k
        while i > 0 and _in_colour(m[i - 1], opposite):
            i -= 1
        s.level, s.top = m[i].open, edge


def four_hour_agrees(h4: Sequence[Candle], since: int, closed_by: int, direction: str) -> bool:
    """A 4h candle in the trade's colour was still trading at since (unix) and closed by closed_by."""
    return any(
        _in_colour(c, direction) for c in h4 if c.time > since - BAR_4H_SECONDS and c.time + BAR_4H_SECONDS <= closed_by
    )


def ema_crosses(q: Sequence[Candle]) -> dict[str, set[int]]:
    """The 1h candles (their start, unix) during which a 15m candle closed across the 50 EMA the
    trade's way: for a short, under it from at or over it."""
    emas = ema_series(q, EMA_LENGTH)
    out: dict[str, set[int]] = {"down": set(), "up": set()}
    for j in range(EMA_SETTLE, len(q)):
        was, now = q[j - 1].close - emas[j - 1], q[j].close - emas[j]
        hour = q[j].time - q[j].time % BAR_SECONDS
        if was >= 0 > now:
            out["down"].add(hour)
        elif was <= 0 < now:
            out["up"].add(hour)
    return out


def walk(
    m: Sequence[Candle], h4: Sequence[Candle], crosses: dict[str, set[int]], zone: Zone, a: int, b: int, reach: float
) -> list[Found]:
    """The setups at this zone on 1h candles a to b - 1, oldest first; crosses = ema_crosses'."""
    lo, hi = zone.bottom - reach, zone.top + reach

    def at_zone(c: Candle) -> bool:
        return c.low <= hi and c.high >= lo

    def breaks(s: _Structure, c: Candle, d: str) -> bool:
        """c closes past where the structure's run started, a run that started at the zone."""
        return s.level is not None and lo <= s.level <= hi and _in_colour(c, d) and _past(c.close, s.level, d)

    out = []
    v = None
    for k in range(max(a, 1), b):
        c = m[k]
        if v is None:
            if at_zone(c) and not at_zone(m[k - 1]):
                v = _Visit("down" if m[k - 1].low <= hi else "up", k)
                _note(v.first, v.direction, m, k)
            continue
        d = v.direction
        if c.close > zone.top if d == "down" else c.close < zone.bottom:
            v = None
        elif v.broke is None:
            if breaks(v.first, c, d):
                v.broke = k
            else:
                _note(v.first, d, m, k)
                if not at_zone(c):
                    v = None
        elif k - v.broke > MAX_WAIT:
            v = None
        elif v.back is None:
            if not _past(c.close, v.first.top, d):
                v.back = k
        elif (
            at_zone(c) and _in_colour(c, d) and c.time in crosses[d]
            and four_hour_agrees(h4, m[v.start].time, c.time + BAR_SECONDS, d)
        ):
            out.append(Found(zone, d, v.start, v.broke, v.back, k))
            v = None
        elif not at_zone(c):
            v.back = None  # gone from the zone: another return is waited for
    return out


def find_setups(
    m: Sequence[Candle], h4: Sequence[Candle], q: Sequence[Candle], days: Sequence[Candle], since: int = 0
) -> list[Found]:
    """Every setup on these 1h candles from since (unix) on, oldest first. q are the closed 15m
    candles, days the closed forex days; each day's candles use the zones of the days closed
    before it."""
    crosses = ema_crosses(q)
    day_times = [d.time for d in days]
    counts = [bisect.bisect_right(day_times, c.time - DAY) for c in m]
    found: dict[tuple[int, str], Found] = {}
    i = bisect.bisect_left([c.time for c in m], since)
    while i < len(m):
        first, end = bisect.bisect_left(counts, counts[i]), bisect.bisect_right(counts, counts[i])
        if first > 0:
            reach = REACH * typical_candle(m[max(0, first - TYPICAL_BARS) : first]) * m[first - 1].close
            closed = days[: counts[i]]
            window = closed[-DAILY_WINDOW:]  # the days strong_zones' turns are counted in
            for zone in strong_zones(closed):
                for f in walk(m, h4, crosses, zone, first - VISIT_BARS, end, reach):
                    if f.entry >= i:
                        f.turns = [window[j].time for j in zone.touch_idx]
                        found.setdefault((f.entry, f.direction), f)
        i = end
    return [found[k] for k in sorted(found)]


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def setup_from(
    symbol: str, name: Optional[str], df: pd.DataFrame, m: Sequence[Candle], days: Sequence[Candle], f: Found
) -> ZoneSetup:
    """The alert on the setup found; df = the 1h bars, days = the closed forex days."""
    candles = m[: f.entry + 1]
    today = day_so_far(candles)
    before = [d for d in days if d.time < today.time]
    return ZoneSetup(
        symbol=symbol,
        name=name,
        direction=f.direction,
        zone_bottom=_round_sig(f.zone.bottom),
        zone_top=_round_sig(f.zone.top),
        touch_count=len(f.zone.touch_idx),
        time=_iso(m[f.entry].time),
        key=f"dz1h:{symbol}:{f.direction}:{_iso(m[f.visit].time)}",
        candles_4h=_to_candles(group_4h(df[df.index <= pd.Timestamp(m[f.entry].time, unit="s", tz="UTC")])),
        candles_1h=list(candles),
        strong=confirms(before, f.direction),
        broke=_iso(m[f.broke].time),
        candles_1d=[*before[-DAILY_WINDOW:], today],
        turns=[_iso(t) for t in f.turns],
    )


def _fetch(symbol: str, interval: str = TIMEFRAME, period: str = PERIOD) -> Optional[pd.DataFrame]:
    """The pair's bars; a busy Yahoo sometimes answers with nothing, so it's asked again."""
    for attempt in range(FETCH_TRIES):
        if attempt:
            time.sleep(FETCH_PAUSE_SECONDS)
        try:
            df = yf.Ticker(symbol).history(interval=interval, period=period)
        except Exception:
            df = None
        if df is not None and not df.empty:
            return df
    logger.warning("Daily zone 1h scan: no %s bars for %s", interval, symbol)
    return None


def _closed(df: pd.DataFrame, seconds: int) -> pd.DataFrame:
    """Yahoo includes the candle still trading; the alert counts on closed ones."""
    return df.iloc[:-1] if df.index[-1].timestamp() + seconds > time.time() else df


def _fetch_15m(symbol: str) -> list[Candle]:
    df = _fetch(symbol, EMA_TIMEFRAME, EMA_PERIOD)
    return [] if df is None else _to_candles(_closed(df, EMA_BAR_SECONDS))


def _evaluate(symbol: str, name: Optional[str]) -> Optional[ZoneSetup]:
    df = _fetch(symbol)
    if df is None:
        return None
    df = _closed(df, BAR_SECONDS)
    m = _to_candles(df)
    if not m or time.time() - (m[-1].time + BAR_SECONDS) > BAR_SECONDS:
        return None  # no fresh candle (the weekend, or Yahoo lags)
    days = daily_candles(df, ALL_DAYS)
    found = find_setups(m, _to_candles(group_4h(df)), _fetch_15m(symbol), days, m[-1].time)
    return setup_from(symbol, name, df, m, days, found[-1]) if found else None


def scan() -> list[ZoneSetup]:
    """One pair at a time: at the same time, a year of 1h bars came back empty for 4 pairs of 20."""
    results = [s for sym, name in UNIVERSE if (s := _evaluate(sym, name)) is not None]
    results.sort(key=lambda s: (not s.strong, -s.touch_count))
    return results


def history_setups(
    symbol: str, name: Optional[str], df: pd.DataFrame, q: Sequence[Candle], since: int
) -> list[ZoneSetup]:
    """Every setup the live run would have sent on these 1h bars (q = the 15m candles) from since
    (unix) on, oldest first, each with the rest of its day."""
    m = _to_candles(df)
    days = daily_candles(df, ALL_DAYS)
    out = []
    for f in find_setups(m, _to_candles(group_4h(df)), q, days, since):
        s = setup_from(symbol, name, df, m, days, f)
        s.after = alert_chart.rest_of_day(m, f.entry)
        out.append(s)
    return out


def format_message(s: ZoneSetup, example: bool = False) -> str:
    text = telegram.alert_caption(s.symbol, s.name, s.direction == "up", STRONG_TAG if s.strong else "")
    if example:
        when = datetime.fromisoformat(s.time) + timedelta(seconds=BAR_SECONDS)
        text += f"\n🕘 דוגמה מההיסטוריה · {when.astimezone(ZoneInfo(settings.alert_timezone)):%d/%m %H:%M}"
    return text


def render(s: ZoneSetup) -> bytes:
    """The daily, 4h and 1h charts, each with the daily zone: on the daily one its turns and the
    return circled, on the 1h one the break and the alert boxed."""
    title = s.name or s.symbol
    zone = [(s.zone_bottom, s.zone_top)]
    return alert_chart.render_side_by_side(
        ((s.candles_1d, f"{title} · 1d", s.zone_bottom, s.zone_top, None), dict(
            touches=[*s.turns, _iso(s.candles_1d[-1].time)], legend=[("Strong daily zone", alert_chart.ZONE_LINE)],
            time_format="%d/%m", time_labels=4, min_bars=DAILY_SHOWN, max_bars=DAILY_WINDOW + 1, lead_bars=3,
        )),
        ((s.candles_4h, f"{title} · 4h", None, None, None), dict(
            extra_zones=zone, time_format="%d/%m\n%H:%M", time_labels=5, min_bars=SHOWN_4H, max_bars=SHOWN_4H,
        )),
        ((s.candles_1h, f"{title} · 1h", None, None, None), dict(
            extra_zones=zone, boxes=[(s.broke, s.broke), (s.time, s.time)], after=s.after,
            time_format="%d/%m\n%H:%M", time_labels=5, min_bars=SHOWN_1H, max_bars=MAX_WAIT + BREAK_LEAD + 1,
            lead_bars=BREAK_LEAD,
        )),
        size=PICTURE_SIZE, divider=True, widths=WIDTHS,
    )


def send_alert(s: ZoneSetup, example: bool = False) -> bool:
    """The two charts with the message as their caption — or the message alone if the
    picture can't be drawn."""
    try:
        png = render(s)
    except Exception:
        logger.exception("Daily zone 1h alert: chart picture failed for %s, sending text only", s.symbol)
        png = None
    return trade_tracker.send_alert(png, format_message(s, example), BOT, s.key, s.symbol, s.name, None)


def run_daily_zone_alerts(db: Session) -> int:
    """Scans the 1h candle that just closed and sends each new setup; returns how many went out."""
    with _run_lock:
        found = scan()
        if not telegram.is_configured():
            logger.info("Daily zone 1h alerts: Telegram not configured, %d found but not sent", len(found))
            return 0
        sent = send_new(db, found, send_alert, len(found))
        logger.info("Daily zone 1h alerts: %d setups in %d pairs, %d sent", len(found), len(UNIVERSE), sent)
        return sent


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    since = int(time.time()) - EXAMPLE_DAYS * DAY
    found = []
    for sym, name in UNIVERSE:
        df = _fetch(sym)
        if df is not None:
            found += history_setups(sym, name, df, _fetch_15m(sym), since)
    found.sort(key=lambda s: s.time)
    print(f"{len(found)} setups in the past month ({sum(s.direction == 'down' for s in found)} shorts)")
    for s in found[-count:] if count else []:
        print(" ", s.time, s.symbol, s.direction, "sent" if send_alert(s, example=True) else "FAILED")
        time.sleep(SEND_INTERVAL_SECONDS)
