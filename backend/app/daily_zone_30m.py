"""The forex bot (the main bot) from 4/11/26 to 5/11/26, off since then (code kept): the bot
sends daily_zone_1h.py's alerts instead. As the user sharpened it, in place of daily_zone_4h.py's
4h turn and 1h confirmation:

1. Daily: price comes within REACH of a zone it turned at three times or more
   (daily_zone_4h.daily_zones). From below it's resistance, a short; from above, support,
   a long.
2. 30m: a candle touches or crosses the 50 EMA, then the break: a candle in the trade's
   colour closes past the body of the opposite candle right before it, and past the EMA.
   For a short, a red candle under the green one's body and under the EMA. The touch counts
   up to EMA_BARS candles back, the break's own included (my default). Once a visit; a close
   through the zone doesn't count.

The 4h chart is only in the picture, not a condition (the user's pick, 4/11/26). A signal
only: no entry, stop or target, no trade watched (the bots' rule since 4/11/26). The daily
candle (daily_candle.py) holds nothing back; when it agrees, the alert says it's a strong
one (the user's ask, 1/11/26).

The picture is the daily, 4h and 30m charts side by side, a thick black line between them.
Its daily candles are forex days built from 1h bars (daily_candle.py): Yahoo's own have
hardly a body (2% of their range, against 45%), though their highs and lows, which the zones
come from, match.
Runs every half hour, on the 30m candle closes (scheduler.py), and on demand; every visit is
sent once, remembered in the sent_alerts table. `python -m app.daily_zone_30m N` sends the
last N setups of the past 60 days (Yahoo's limit for 30m bars), marked as examples.

On those 60 days, 20 pairs, before the spread: ~17 signals a week; with a stop behind the
visit's wicks, 1:3, 26% reached the target, +0.02R a signal."""
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
from .candles import Candle, _to_candles, group_4h
from .config import settings
from .daily_candle import _forex_day, confirms, daily_candles
from .daily_zone_4h import BOT, DAILY_PERIOD, STRONG_TAG, UNIVERSE, daily_zones
from .nasdaq_scalp import ema_series
from .technical_scan import _round_sig
from .trade_plan import is_entry_candle
from .zone_touch_scan import send_new
from .zones import Zone, typical_candle

logger = logging.getLogger(__name__)

TIMEFRAME = "30m"
BAR_SECONDS = 1800
DAY = 86400
YEAR_DAYS = 366
LIVE_PERIOD = "1mo"  # the EMA, the typical candles, the 4h chart, the two weeks daily_candles reads
HISTORY_PERIOD = "60d"
# A candle this near the zone (in typical 30m candles) came to it.
REACH = 0.5
TYPICAL_BARS = 200
# Enough candles for the EMA to settle.
MIN_BARS = 100
# The EMA touch counts this many candles back, the break's own included.
EMA_BARS = 3
# Candles shown at least, on each chart.
SHOWN_1D = 120
SHOWN_4H = 60
SHOWN_30M = 60
PICTURE_SIZE = (18, 6)
SEND_INTERVAL_SECONDS = 1.1


@dataclass
class Found:
    zone: Zone
    direction: str  # "down" = short | "up" = long
    visit: int  # the visit's first 30m candle
    entry: int  # the break
    touch: Optional[int]  # the EMA touch before the break; None if only the break touched it


@dataclass
class ZoneSetup:
    symbol: str
    name: Optional[str]
    direction: str
    zone_bottom: float
    zone_top: float
    touch_count: int  # the daily zone's
    touch_time: Optional[str]  # Found.touch's (ISO)
    time: str  # the break's; the alert goes out when it closes
    key: str  # one alert per visit
    candles_1d: list[Candle] = field(default_factory=list, repr=False)  # closed, then the day so far
    candles_4h: list[Candle] = field(default_factory=list, repr=False)  # up to the break, the last one forming
    candles_30m: list[Candle] = field(default_factory=list, repr=False)  # up to the break
    emas: list[float] = field(default_factory=list, repr=False)  # aligned with candles_30m and after
    strong: bool = False  # the daily candle agrees (daily_candle.confirms)
    after: list[Candle] = field(default_factory=list, repr=False)  # an example's: the rest of its day


_run_lock = threading.Lock()


def touched(m: Sequence[Candle], emas: Sequence[float], k: int) -> bool:
    """Candle k touched the EMA, or went across it since the candle before."""
    return m[k].low <= emas[k] <= m[k].high or (m[k - 1].close - emas[k - 1]) * (m[k].close - emas[k]) < 0


def is_break(m: Sequence[Candle], emas: Sequence[float], j: int, direction: str) -> bool:
    """Candle j is the break (step 2), the EMA touched on it or just before it."""
    down = direction == "down"
    prev, c = m[j - 1], m[j]
    opposite = prev.close >= prev.open if down else prev.close <= prev.open
    past_ema = c.close < emas[j] if down else c.close > emas[j]
    return (
        opposite
        and past_ema
        and is_entry_candle(prev, c, direction)
        and any(touched(m, emas, k) for k in range(max(1, j - EMA_BARS + 1), j + 1))
    )


def find_at(m: Sequence[Candle], emas: Sequence[float], zones: Sequence[Zone], j: int, reach: float) -> Optional[Found]:
    """The setup on candle j: price at one of the zones, on j or the candle before, and j the
    visit's first break."""
    for zone in zones:
        lo, hi = zone.bottom - reach, zone.top + reach

        def at_zone(c: Candle) -> bool:
            return c.low <= hi and c.high >= lo

        a = j if at_zone(m[j]) else j - 1
        if not at_zone(m[a]):
            continue
        while a > 0 and at_zone(m[a - 1]):
            a -= 1
        if a == 0:
            continue
        direction = "up" if m[a - 1].low > hi else "down"

        def fires(k: int) -> bool:
            through = m[k].close > zone.top if direction == "down" else m[k].close < zone.bottom
            return not through and is_break(m, emas, k, direction)

        if fires(j) and not any(fires(k) for k in range(max(1, a), j)):
            touch = next((k for k in range(j - 1, j - EMA_BARS, -1) if k >= 1 and touched(m, emas, k)), None)
            return Found(zone, direction, a, j, touch)
    return None


def find_setups(m: Sequence[Candle], emas: Sequence[float], days: Sequence[Candle], since: int = 0) -> list[Found]:
    """Every setup on these 30m candles from since (unix) on, oldest first. days are Yahoo's
    daily candles; each candle uses the zones of the days closed before it."""
    times = [c.time for c in m]
    day_times = [d.time for d in days]
    zones_by_count: dict[int, list[Zone]] = {}
    out = []
    for j in range(max(MIN_BARS, bisect.bisect_left(times, since)), len(m)):
        count = bisect.bisect_right(day_times, m[j].time - DAY)
        if count not in zones_by_count:
            zones_by_count[count] = daily_zones(days[:count])
        unit = typical_candle(m[max(0, j - TYPICAL_BARS) : j]) * m[j - 1].close
        if found := find_at(m, emas, zones_by_count[count], j, REACH * unit):
            out.append(found)
    return out


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _forex_day_of(c: Candle):
    return _forex_day(pd.Timestamp(c.time, unit="s", tz="UTC"))


def day_so_far(m: Sequence[Candle]) -> Candle:
    """The forex day still forming: the last of these 30m candles and those of its day before it."""
    day = _forex_day_of(m[-1])
    a = len(m) - 1
    while a > 0 and _forex_day_of(m[a - 1]) == day:
        a -= 1
    bars = m[a:]
    return Candle(
        bars[0].time, bars[0].open, max(c.high for c in bars), min(c.low for c in bars), bars[-1].close,
        sum(c.volume or 0 for c in bars),
    )


def four_hours(df: pd.DataFrame) -> list[Candle]:
    """4h candles from these 30m bars, as get_4h_dataframe() builds them from 1h bars."""
    hourly = df.resample("1h").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Open"])
    return _to_candles(group_4h(hourly))


def setup_from(
    symbol: str, name: Optional[str], df: pd.DataFrame, m: Sequence[Candle], emas: Sequence[float],
    forex_days: Sequence[Candle], f: Found, daily: Sequence[Candle],
) -> ZoneSetup:
    """The alert on the break found; df = the 30m bars; forex_days = the daily chart's closed
    candles; daily = the closed forex days (daily_candle.py) before its day, which make it
    strong or not."""
    j = f.entry
    entry = m[j].time
    candles = m[: j + 1]
    today = day_so_far(candles)
    return ZoneSetup(
        symbol=symbol,
        name=name,
        direction=f.direction,
        zone_bottom=_round_sig(f.zone.bottom),
        zone_top=_round_sig(f.zone.top),
        touch_count=len(f.zone.touch_idx),
        touch_time=_iso(m[f.touch].time) if f.touch is not None else None,
        time=_iso(entry),
        key=f"dz30m:{symbol}:{_iso(m[f.visit].time)}:{_round_sig(f.zone.bottom)}",
        candles_1d=[*(d for d in forex_days if d.time < today.time), today],
        candles_4h=four_hours(df[df.index <= pd.Timestamp(entry, unit="s", tz="UTC")]),
        candles_30m=list(candles),
        emas=list(emas[: j + 1]),
        strong=confirms(daily, f.direction),
    )


def _fetch(symbol: str, interval: str, period: str) -> Optional[pd.DataFrame]:
    try:
        df = yf.Ticker(symbol).history(interval=interval, period=period)
    except Exception:
        logger.warning("Daily zone 30m scan: %s history fetch failed for %s", interval, symbol)
        return None
    return None if df is None or df.empty else df


def _evaluate(symbol: str, name: Optional[str]) -> Optional[ZoneSetup]:
    df = _fetch(symbol, TIMEFRAME, LIVE_PERIOD)
    if df is None:
        return None
    # Yahoo includes the candle still trading; the break counts on a closed one.
    if df.index[-1].timestamp() + BAR_SECONDS > time.time():
        df = df.iloc[:-1]
    m = _to_candles(df)
    if not m or time.time() - (m[-1].time + BAR_SECONDS) > BAR_SECONDS:
        return None  # no fresh candle (the weekend, or Yahoo lags)
    dd = _fetch(symbol, "1d", DAILY_PERIOD)
    if dd is None:
        return None
    days, emas = _to_candles(dd), ema_series(m)
    found = find_setups(m, emas, days, m[-1].time)
    if not found:
        return None
    hourly = _fetch(symbol, "1h", DAILY_PERIOD)
    forex_days = daily_candles(hourly, YEAR_DAYS) if hourly is not None else days
    return setup_from(symbol, name, df, m, emas, forex_days, found[-1], daily_candles(df))


def scan() -> list[ZoneSetup]:
    results: list[ZoneSetup] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate, sym, name) for sym, name in UNIVERSE]
        for fut in as_completed(futures):
            if (result := fut.result()) is not None:
                results.append(result)
    results.sort(key=lambda s: (not s.strong, -s.touch_count))
    return results


def history_setups(
    symbol: str, name: Optional[str], df: pd.DataFrame, dd: pd.DataFrame, hourly: pd.DataFrame
) -> list[ZoneSetup]:
    """Every setup the live run would have sent on these 30m bars (df), daily bars (dd) and
    1h bars (hourly, for the daily chart), oldest first, each with the rest of its day."""
    m = _to_candles(df)
    emas = ema_series(m)
    days_of_bars = pd.Series([_forex_day(ts) for ts in df.index], index=df.index)
    forex_days = daily_candles(hourly, YEAR_DAYS)
    out = []
    for f in find_setups(m, emas, _to_candles(dd)):
        day = _forex_day_of(m[f.entry])
        s = setup_from(symbol, name, df, m, emas, forex_days, f, daily_candles(df[days_of_bars < day]))
        s.after = alert_chart.rest_of_day(m, f.entry)
        s.emas = list(emas[: f.entry + 1 + len(s.after)])
        out.append(s)
    return out


def format_message(s: ZoneSetup, example: bool = False) -> str:
    text = telegram.alert_caption(s.symbol, s.name, s.direction == "up", STRONG_TAG if s.strong else "")
    if example:
        when = datetime.fromisoformat(s.time) + timedelta(seconds=BAR_SECONDS)
        text += f"\n🕘 דוגמה מההיסטוריה · {when.astimezone(ZoneInfo(settings.alert_timezone)):%d/%m %H:%M}"
    return text


def render(s: ZoneSetup) -> bytes:
    """The daily, 4h and 30m charts, each with the daily zone; the 30m one with the EMA, its
    touch and the break."""
    title = s.name or s.symbol
    zone = [(s.zone_bottom, s.zone_top)]
    return alert_chart.render_side_by_side(
        ((s.candles_1d, f"{title} · 1D", None, None, None), dict(
            extra_zones=zone, time_format="%d/%m", time_labels=5, min_bars=SHOWN_1D,
        )),
        ((s.candles_4h, f"{title} · 4h", None, None, None), dict(
            extra_zones=zone, time_format="%d/%m", time_labels=5, min_bars=SHOWN_4H,
        )),
        ((s.candles_30m, f"{title} · 30m · EMA 50", None, None, None), dict(
            extra_zones=zone, direction=s.direction, breakout=s.time, touches=[s.touch_time] if s.touch_time else [],
            line=s.emas, after=s.after, time_format="%d/%m\n%H:%M", time_labels=5, min_bars=SHOWN_30M,
        )),
        size=PICTURE_SIZE, divider=True,
    )


def send_alert(s: ZoneSetup, example: bool = False) -> bool:
    """The three charts with the message as their caption — or the message alone if the
    picture can't be drawn."""
    try:
        png = render(s)
    except Exception:
        logger.exception("Daily zone 30m alert: chart picture failed for %s, sending text only", s.symbol)
        png = None
    return trade_tracker.send_alert(png, format_message(s, example), BOT, s.key, s.symbol, s.name, None)


def run_daily_zone_alerts(db: Session) -> int:
    """Scans the 30m candle that just closed and sends each new setup; returns how many went out."""
    with _run_lock:
        found = scan()
        if not telegram.is_configured():
            logger.info("Daily zone 30m alerts: Telegram not configured, %d found but not sent", len(found))
            return 0
        sent = send_new(db, found, send_alert, len(found))
        logger.info("Daily zone 30m alerts: %d setups in %d pairs, %d sent", len(found), len(UNIVERSE), sent)
        return sent


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    found = []
    for sym, name in UNIVERSE:
        df, dd, hourly = _fetch(sym, TIMEFRAME, HISTORY_PERIOD), _fetch(sym, "1d", "2y"), _fetch(sym, "1h", DAILY_PERIOD)
        if df is not None and dd is not None and hourly is not None:
            found += history_setups(sym, name, df, dd, hourly)
    found.sort(key=lambda s: s.time)
    print(f"{len(found)} setups in the past 60 days ({sum(s.direction == 'down' for s in found)} shorts)")
    for s in found[-count:] if count else []:
        print(" ", s.time, s.symbol, s.direction, "sent" if send_alert(s, example=True) else "FAILED")
        time.sleep(SEND_INTERVAL_SECONDS)
