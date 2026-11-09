"""The stocks bot's weekly strategy (the user's ask, 2/11/26, with an LLY weekly picture),
in place of SHILO (the user's pick, option 2; shilo.py stays in the code).

On weekly candles: a zone price turned at MIN_TOUCHES times or more (zones.py, on the last
WINDOW_BARS weeks) — "touched it several times and was pushed off it", the touches close
together in time (the user's ask after the first examples: ~2 years back was too far).
A touch is a week whose low (high) is under (over) the one on each side. Price comes back
to it, and a week closes the trade's way past the body of the opposite candle before it
(trade_plan.zone_entry: from above it's support, a long; from below, resistance, a
short), with the Stochastic RSI (14, 14, 3) %K on that candle under 20 for a long, over
80 for a short — the same rule as SHILO and the Nasdaq bot's model 2. A signal only, since
the user turned it back on in place of the monthly 50 EMA (8/11/26): no entry, stop or target
drawn, no trade watched (the bots' rule since 4/11/26). The 1:3 trade (in on the close, the
stop behind the zone and the visit's wicks) only drops shorts with a target under 0.

The user's LLY: the zone at ~1,100-1,150 touched on 22/6, 13/7 and 3/8/26, the week of
14/9/26 closed green over the red body before it with %K at 6.5 — a long at ~1,153.
On ~3.4 years of 100 big names: ~3 signals a week, longs +0.13R a trade at 1:3, shorts
-0.33R (zones from 3 years back: +0.38R and -0.41R) — the user kept both.

Checked once a week on Sunday evening in Israel, before the week's trading opens
(scheduler.py), on ~4,500 US stocks (Nasdaq, the NYSE and NYSE American) and ~500 Tel Aviv
ones (the user's ask, 2/11/26: a huge list); every signal is sent once (sent_alerts).
`python -m app.weekly_zone N` sends the last N signals of the big names, marked as
examples."""
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
from yfinance import EquityQuery

from . import alert_chart, telegram, trade_tracker
from .candles import Candle, _to_candles
from .config import settings
from .misc_strategies import LARGE_CAPS
from .scanner import NASDAQ_EXCHANGES, _fetch_all_quotes
from .technical_scan import _get_stock_universe, _round_sig, _stoch_rsi, _stock_fetch_limiter
from .trade_plan import TradePlan, zone_entry, zone_plan
from .zone_touch_scan import send_new
from .zones import RETEST_REACH, detect_zones

logger = logging.getLogger(__name__)

BOT = "stocks"
TAG = "שבועי"
NY = ZoneInfo("America/New_York")
WEEK = 7 * 86400
HISTORY_PERIOD = "5y"
# Zones come from the last this-many weeks (half a year), so the touches are recent.
WINDOW_BARS = 26
MIN_TOUCHES = 3
PIVOT_BARS = 1
REACH = RETEST_REACH
STOCH_OVERSOLD = 20.0
STOCH_OVERBOUGHT = 80.0
# The stocks: every US one trading 100K shares a day (LLY is on the NYSE), and Tel Aviv's.
UNIVERSE_SIZE = 6000
UNIVERSE_MIN_VOLUME = 100_000
EXCHANGES = [*NASDAQ_EXCHANGES, "NYQ", "ASE"]
TASE_MIN_VOLUME = 1_000
MAX_ALERTS = 60
PICTURE_BARS = 60
AFTER_BARS = 40  # an example's weeks shown after its entry
# The picture, near today (the user's ask, 8/11/26): the zone's half year of weeks and about
# three months of days, and an example's weeks and days after its entry.
WEEK_BARS = 34
DAY_BARS = 65
AFTER_WEEKS = 8
AFTER_DAYS = 15


@dataclass
class Signal:
    symbol: str
    name: Optional[str]
    direction: str  # "up" = long | "down" = short
    i: int  # the entry week, in candles
    zone_bottom: float
    zone_top: float
    touch_times: list[str]
    plan: TradePlan
    candles: list[Candle] = field(default_factory=list, repr=False)  # up to the entry week
    k: list[float] = field(default_factory=list, repr=False)
    d: list[float] = field(default_factory=list, repr=False)
    after: list[Candle] = field(default_factory=list, repr=False)  # an example's: the weeks after it

    @property
    def time(self) -> int:
        return self.candles[self.i].time

    @property
    def key(self) -> str:
        return f"weekly:{self.symbol}:{self.time}"


_run_lock = threading.Lock()


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def signal_at(candles: Sequence[Candle], k: Sequence[float], i: int) -> Optional[tuple]:
    """(direction, zone, the visit's first candle, trade) when week i is the entry — seeing
    only the weeks up to it."""
    lo = max(0, i + 1 - WINDOW_BARS)
    window = candles[lo : i + 1]
    for zone in detect_zones(window, min_touches=MIN_TOUCHES, pivot_bars=PIVOT_BARS):
        found = zone_entry(window, zone.bottom, zone.top, REACH * zone.unit)
        if found:
            break
    else:
        return None
    direction, a = found
    if not (k[i] < STOCH_OVERSOLD if direction == "up" else k[i] > STOCH_OVERBOUGHT):
        return None
    trade = zone_plan(window, a, direction, zone.bottom, zone.top, zone.unit)
    if trade is None or trade.target <= 0:  # a short's 1:3 on a cheap stock can go under 0
        return None
    zone.touch_idx = [lo + t for t in zone.touch_idx]
    return direction, zone, lo + a, trade


def _signal(symbol: str, name: Optional[str], candles: Sequence[Candle], k, d, i: int) -> Optional[Signal]:
    got = signal_at(candles, k, i)
    if got is None:
        return None
    direction, zone, a, trade = got
    marks = sorted({*zone.touch_idx, *([a] if a < i else [])})
    return Signal(
        symbol, name, direction, i, _round_sig(zone.bottom), _round_sig(zone.top),
        [_iso(candles[t].time) for t in marks], trade, list(candles[: i + 1]), list(k[: i + 1]), list(d[: i + 1]),
    )


def stoch(candles: Sequence[Candle]) -> tuple[list[float], list[float]]:
    k, d = _stoch_rsi(pd.Series([c.close for c in candles]))
    return k.tolist(), d.tolist()


def find_signals(symbol: str, name: Optional[str], candles: Sequence[Candle]) -> list[Signal]:
    """Every signal on these weeks, oldest first."""
    k, d = stoch(candles)
    out = []
    for i in range(WINDOW_BARS // 2, len(candles)):
        if s := _signal(symbol, name, candles, k, d, i):
            s.after = list(candles[i + 1 :])
            out.append(s)
    return out


def week_closed(c: Candle, now: float) -> bool:
    """Yahoo's week starts on Monday and includes the days still trading; it's closed at
    Friday's New York close (a Friday holiday's week too, a day late; Tel Aviv's closes
    earlier). The Monday is read at midday UTC: a Tel Aviv week starts on Sunday evening
    in New York."""
    day = datetime.fromtimestamp(c.time + 43200, timezone.utc).date()
    monday = datetime(day.year, day.month, day.day, tzinfo=NY)
    return now >= (monday + timedelta(days=4, hours=16)).timestamp()


def fetch(symbol: str, period: str = HISTORY_PERIOD) -> list[Candle]:
    """The symbol's closed weeks."""
    _stock_fetch_limiter.wait()
    try:
        df = yf.Ticker(symbol).history(interval="1wk", period=period)
    except Exception:
        logger.warning("Weekly zone scan: history fetch failed for %s", symbol)
        return []
    candles = _to_candles(df.dropna()) if df is not None and not df.empty else []
    return candles if candles and week_closed(candles[-1], time.time()) else candles[:-1]


def fetch_days(symbol: str) -> list[Candle]:
    """The symbol's last year of days, for the picture; none if Yahoo has none."""
    _stock_fetch_limiter.wait()
    try:
        df = yf.Ticker(symbol).history(interval="1d", period="1y")
    except Exception:
        logger.warning("Weekly zone: daily history fetch failed for %s", symbol)
        return []
    return _to_candles(df.dropna()) if df is not None and not df.empty else []


def _evaluate(symbol: str, name: Optional[str]) -> Optional[Signal]:
    """The signal on the last closed week, if it's from the week just ended."""
    candles = fetch(symbol)
    if len(candles) < WINDOW_BARS // 2 or time.time() - candles[-1].time > 2 * WEEK:
        return None
    k, d = stoch(candles)
    return _signal(symbol, name, candles, k, d, len(candles) - 1)


def _tase_universe() -> list[tuple[str, Optional[str]]]:
    """Tel Aviv's stocks — the screener lists its bonds too, but only stocks have a market cap."""
    query = EquityQuery("and", [
        EquityQuery("eq", ["region", "il"]),
        EquityQuery("is-in", ["exchange", "TLV"]),
        EquityQuery("gt", ["dayvolume", TASE_MIN_VOLUME]),
    ])
    try:
        quotes = _fetch_all_quotes(query, sort_field="dayvolume", sort_asc=False, fetch_cap=2000)
    except Exception:
        logger.exception("Weekly zone scan: Tel Aviv screener request failed")
        return []
    return [(q["symbol"], q.get("shortName") or q.get("longName")) for q in quotes if q.get("symbol") and q.get("marketCap")]


def scan() -> list[Signal]:
    universe = _get_stock_universe(UNIVERSE_SIZE, min_volume=UNIVERSE_MIN_VOLUME, exchanges=EXCHANGES)
    universe += _tase_universe()
    if not universe:
        logger.warning("Weekly zone scan: empty stock universe (screener unavailable?)")
    found: list[Signal] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate, sym, name) for sym, name in universe]
        for fut in as_completed(futures):
            if (s := fut.result()) is not None:
                found.append(s)
    found.sort(key=lambda s: -len(s.touch_times))
    logger.info("Weekly zone scan: %d signals in %d stocks", len(found), len(universe))
    return found


def format_message(s: Signal, example: bool = False) -> str:
    text = telegram.alert_caption(s.symbol, None, s.direction == "up", TAG)
    if example:
        text += f"\n🕘 דוגמה מההיסטוריה · השבוע של {datetime.fromtimestamp(s.time, NY):%d/%m/%y}"
    return text


def picture(s: Signal) -> bytes:
    """The weeks beside the days (the user's ask, 8/11/26), near today: on the weeks the zone
    and its touches, the entry week's arrow and the Stochastic RSI under them — as on the
    user's picture, without its trade; on the days the zone, the entry week's days framed and
    the arrow on its last. The weeks alone if the days can't be had."""
    weeks = (
        (s.candles, f"{s.symbol} · 1W", s.zone_bottom, s.zone_top, s.touch_times[0]),
        dict(
            direction=s.direction, breakout=_iso(s.time), touches=s.touch_times, stoch=(s.k, s.d),
            time_format="%d/%m", min_bars=WEEK_BARS, max_bars=WEEK_BARS, after=s.after[:AFTER_WEEKS],
        ),
    )
    end = s.time + WEEK
    days, later = [], []
    for c in fetch_days(s.symbol):
        (days if c.time < end else later).append(c)
    week_days = [c for c in days if c.time >= s.time]
    if not week_days:
        return alert_chart.render(*weeks[0], **weeks[1])
    first, last = _iso(week_days[0].time), _iso(week_days[-1].time)
    return alert_chart.render_side_by_side(weeks, (
        (days, f"{s.symbol} · 1D", s.zone_bottom, s.zone_top, None),
        dict(
            direction=s.direction, breakout=last, boxes=[(first, last)],
            time_format="%d/%m", min_bars=DAY_BARS, max_bars=DAY_BARS, after=later[:AFTER_DAYS],
        ),
    ), divider=True)


def send_alert(s: Signal, example: bool = False) -> bool:
    try:
        png = picture(s)
    except Exception:
        logger.exception("Weekly zone: chart picture failed for %s, sending text only", s.key)
        png = None
    return trade_tracker.send_alert(png, format_message(s, example), BOT, s.key, s.symbol, None, None)


def run_weekly_zone(db: Session) -> int:
    """Scans the week just closed and sends each new signal; returns how many went out."""
    with _run_lock:
        found = scan()
        return send_new(db, found, send_alert, MAX_ALERTS)


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    found = [s for symbol in LARGE_CAPS for s in find_signals(symbol, None, fetch(symbol))]
    found.sort(key=lambda s: s.time)
    print(f"{len(found)} signals")
    for s in found[-count:]:
        print(" ", s.symbol, _iso(s.time), s.direction, "sent" if send_alert(s, example=True) else "FAILED")
        time.sleep(1.1)
