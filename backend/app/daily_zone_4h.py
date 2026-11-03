"""The forex 4h bot (the main bot), as the user sharpened it on 31/10/26 — three charts
have to agree before an alert goes out:

1. Daily: a zone price turned at DAILY_MIN_TOUCHES times or more (zones.py, on the last
   DAILY_WINDOW daily candles) — "a strong place".
2. 4h: price comes to that zone and a 4h candle closes past the body of the opposite
   candle before it (trade_plan.zone_entry) — the market turns there. From below it's
   resistance, a short; from above, support, a long.
3. 1h, after that 4h candle closed (the user's pick, option 1): within CONFIRM_BARS
   hours, either a big 1h candle the trade's way (a body of BIG_BODY typical candles,
   closing past the body before it), or a breakout of the last 1h swing then a retest
   of it and an entry candle (trade_plan.is_entry_candle). The alert, on that candle:
   the stop behind the daily zone and the visit's wicks, 1:3 (trade_plan.zone_plan).

The daily candle (daily_candle.py) no longer holds an alert back — both versions stay for
now (the user's ask, 1/11/26): when it agrees with the trade, the alert says it's a
strong one and the stop is as above; when it doesn't, the stop is behind the visit's
wicks only.

A 1h close past the zone's far side ends the setup. The picture is the 4h chart beside
the 1h one, in three colours: the daily zone blue, the 4h turn orange, the 1h
confirmation purple. Replaces the 3-touch 4h zone alerts (zone_touch_scan.py, whose
list stays in the trainer). Runs every hour, on the 1h candle closes (scheduler.py), and
on demand; every 4h turn is sent once, remembered in the sent_alerts table.
`python -m app.daily_zone_4h N` sends the last N setups of the past year, marked as
examples — for the user to judge the strategy by."""
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
from .technical_scan import ALERT_FOREX, _round_sig
from .trade_plan import TradePlan, behind, is_entry_candle, plan, zone_entry, zone_plan
from .zone_touch_scan import send_new
from .zones import BREAK_BUFFER, RETEST_REACH, Zone, detect_zones, typical_candle

logger = logging.getLogger(__name__)

HOUR = 3600
DAY = 24 * HOUR
UNIVERSE = ALERT_FOREX  # the watchlist pairs, as the other forex bots
BOT = "main"  # telegram.bot_chat
LIVE_PERIOD = "1mo"  # 1h bars: the visit, the typical candles, the two weeks daily_candles reads
HISTORY_PERIOD = "1y"
DAILY_PERIOD = "1y"  # Yahoo's daily bars, for the zones
DAILY_WINDOW = 120  # daily candles the zones come from (~6 months)
DAILY_MIN_TOUCHES = 3
# A 4h candle this near the zone (in typical 4h candles) came to it.
REACH = RETEST_REACH
TYPICAL_4H = 100
TYPICAL_1H = 200
# The 1h confirmation has to come within this many hours of the 4h turn's close.
CONFIRM_BARS = 12
# The big candle's body, in typical 1h candles.
BIG_BODY = 1.5
# A 1h swing high/low beats this many candles on each side.
SWING_BARS = 2
# Candles shown at least, on each side of the picture.
SHOWN_4H = 60
SHOWN_1H = 48
SEND_INTERVAL_SECONDS = 1.1
STRONG_TAG = "💪 איתות חזק · הנר היומי מאשר"
DAILY_COLORS = (alert_chart.ZONE_FILL, alert_chart.ZONE_LINE)
TURN_COLOR = alert_chart.BREAK  # orange
CONFIRM_COLOR = alert_chart.TOUCH  # purple
LEGEND = (("Daily zone", alert_chart.ZONE_LINE), ("4h turn", TURN_COLOR), ("1h confirm", CONFIRM_COLOR))


@dataclass
class Confirm:
    kind: str  # "big" = a big 1h candle | "retest" = a breakout of the last swing, then a retest
    idx: int  # the entry candle, in the 1h candles
    level: Optional[float] = None  # the swing broken (a retest's)
    swing: Optional[int] = None  # its candle
    touch: Optional[int] = None  # the retest's first candle back at it


@dataclass
class Found:
    zone: Zone
    direction: str  # "down" = short | "up" = long
    visit: int  # the 4h candle price came to the zone on
    turn: int  # the 4h candle that turned
    visit_1h: int  # the visit's first 1h candle
    confirm: Confirm
    unit_1h: float


@dataclass
class DailyZoneSetup:
    symbol: str
    name: Optional[str]
    direction: str
    zone_bottom: float
    zone_top: float
    touch_count: int  # the daily zone's
    turn_time: str  # the 4h candle that turned (ISO)
    time: str  # the 1h entry candle's; the alert goes out when it closes
    key: str  # one alert per 4h turn
    kind: str  # Confirm.kind
    level: Optional[float] = None
    swing_time: Optional[str] = None
    touch_time: Optional[str] = None
    candles_4h: list[Candle] = field(default_factory=list, repr=False)  # up to the entry, the last one forming
    candles_1h: list[Candle] = field(default_factory=list, repr=False)  # up to the entry
    plan: Optional[TradePlan] = None  # entered on the last 1h candle
    strong: bool = False  # the daily candle agrees (daily_candle.confirms)
    after: list[Candle] = field(default_factory=list, repr=False)  # an example's: the rest of its day, 1h


_run_lock = threading.Lock()


def _beyond(price: float, level: float, direction: str) -> bool:
    """price is past level the trade's way."""
    return price < level if direction == "down" else price > level


def _swing(h1: Sequence[Candle], p: int, direction: str) -> bool:
    """h1[p] is the swing a trade this way breaks out of: a high for a long, a low for a short."""
    if p < SWING_BARS or p + SWING_BARS >= len(h1):
        return False
    around = [*h1[p - SWING_BARS : p], *h1[p + 1 : p + SWING_BARS + 1]]
    if direction == "down":
        return all(c.low > h1[p].low for c in around)
    return all(c.high < h1[p].high for c in around)


def confirmation(
    h1: Sequence[Candle], visit: int, start: int, zone: Zone, direction: str, unit: float
) -> Optional[Confirm]:
    """The first 1h confirmation (step 3) from start, the first candle after the 4h turn:
    a big candle, or the entry after a breakout of the last swing since the visit and a
    retest of it. None if the setup ended first."""
    down = direction == "down"
    far = zone.top if down else zone.bottom
    buffer = BREAK_BUFFER * unit
    level = swing = broke = touch = None
    for j in range(visit, min(len(h1), start + CONFIRM_BARS)):
        c = h1[j]
        if j >= start and _beyond(far, c.close, direction):
            return None  # closed through the zone
        p = j - SWING_BARS  # the swing now confirmed by the candles after it
        if broke is None and p >= visit and _swing(h1[: j + 1], p, direction):
            level, swing = (h1[p].low if down else h1[p].high), p
        if j < start:
            if level is not None and broke is None and _beyond(c.close, level + (-buffer if down else buffer), direction):
                broke = j
            continue
        body = abs(c.close - c.open)
        if is_entry_candle(h1[j - 1], c, direction) and body >= BIG_BODY * unit:
            return Confirm("big", j)
        if level is None:
            continue
        if broke is None:
            if _beyond(c.close, level + (-buffer if down else buffer), direction):
                broke = j
            continue
        if _beyond(level + (buffer if down else -buffer), c.close, direction):
            broke = touch = None  # back under the swing (over it, for a short): no breakout
            continue
        if touch is None and j > broke and (c.high >= level - RETEST_REACH * unit if down else c.low <= level + RETEST_REACH * unit):
            touch = j
        if touch is not None and is_entry_candle(h1[j - 1], c, direction) and _beyond(c.close, level, direction):
            return Confirm("retest", j, level, swing, touch)
    return None


def daily_zones(days: Sequence[Candle]) -> list[Zone]:
    return detect_zones(days[-DAILY_WINDOW:], min_touches=DAILY_MIN_TOUCHES)


def find_setups(
    h1: Sequence[Candle], h4: Sequence[Candle], days: Sequence[Candle], since: int = 0
) -> list[Found]:
    """Every setup on these candles whose 4h turn started at or after since (unix), oldest
    first. days are Yahoo's daily candles; each turn uses the zones of the days closed
    before it."""
    times = [c.time for c in h1]
    day_times = [d.time for d in days]
    zones_by_count: dict[int, list[Zone]] = {}
    out = []
    for r in range(2, len(h4) - 1):  # the turn closed: a 4h candle came after it
        if h4[r].time < since:
            continue
        count = bisect.bisect_right(day_times, h4[r].time - DAY)
        if count not in zones_by_count:
            zones_by_count[count] = daily_zones(days[:count])
        candles = h4[max(0, r + 1 - TYPICAL_4H) : r + 1]
        reach = REACH * typical_candle(candles) * candles[-1].close
        for zone in zones_by_count[count]:
            entry = zone_entry(candles, zone.bottom, zone.top, reach)
            if entry:
                break
        else:
            continue
        direction, a = entry
        visit = r + 1 - len(candles) + a
        start = bisect.bisect_left(times, h4[r + 1].time)
        visit_1h = bisect.bisect_left(times, h4[visit].time)
        if start < TYPICAL_1H // 4:
            continue
        unit = typical_candle(h1[max(0, start - TYPICAL_1H) : start]) * h1[start - 1].close
        confirm = confirmation(h1, visit_1h, start, zone, direction, unit)
        if confirm:
            out.append(Found(zone, direction, visit, r, visit_1h, confirm, unit))
    return out


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def trade_for(candles: Sequence[Candle], f: Found, strong: bool) -> Optional[TradePlan]:
    """In on the last candle's close: a strong setup's stop behind the daily zone and the
    visit's wicks, the rest's behind the wicks only."""
    if strong:
        return zone_plan(candles, f.visit_1h, f.direction, f.zone.bottom, f.zone.top, f.unit_1h)
    entry = candles[-1].close
    return plan(f.direction, entry, behind(candles[f.visit_1h :], f.direction, entry, f.unit_1h))


def setup_from(
    symbol: str, name: Optional[str], df: pd.DataFrame, h1: Sequence[Candle], h4: Sequence[Candle], f: Found,
    daily: Sequence[Candle],
) -> Optional[DailyZoneSetup]:
    """The alert on the 1h entry candle found; df = the 1h bars; daily = the closed daily
    candles (daily_candle.py) before its day, which make it strong or not."""
    strong = confirms(daily, f.direction)
    j = f.confirm.idx
    candles = h1[: j + 1]
    trade = trade_for(candles, f, strong)
    if trade is None:
        return None
    turn_time = _iso(h4[f.turn].time)
    c = f.confirm
    return DailyZoneSetup(
        symbol=symbol,
        name=name,
        direction=f.direction,
        zone_bottom=_round_sig(f.zone.bottom),
        zone_top=_round_sig(f.zone.top),
        touch_count=len(f.zone.touch_idx),
        turn_time=turn_time,
        time=_iso(candles[-1].time),
        key=f"dz4h:{symbol}:{turn_time}",
        kind=c.kind,
        level=_round_sig(c.level) if c.level is not None else None,
        swing_time=_iso(h1[c.swing].time) if c.swing is not None else None,
        touch_time=_iso(h1[c.touch].time) if c.touch is not None else None,
        candles_4h=_to_candles(group_4h(df[df.index <= pd.Timestamp(candles[-1].time, unit="s", tz="UTC")])),
        candles_1h=list(candles),
        plan=trade,
        strong=strong,
    )


def _fetch(symbol: str, interval: str, period: str) -> Optional[pd.DataFrame]:
    try:
        df = yf.Ticker(symbol).history(interval=interval, period=period)
    except Exception:
        logger.warning("Daily zone 4h scan: %s history fetch failed for %s", interval, symbol)
        return None
    return None if df is None or df.empty else df


def _evaluate(symbol: str, name: Optional[str]) -> Optional[DailyZoneSetup]:
    df = _fetch(symbol, "1h", LIVE_PERIOD)
    if df is None:
        return None
    # Yahoo includes the hour still trading; the entry counts on a closed candle.
    if df.index[-1].timestamp() + HOUR > time.time():
        df = df.iloc[:-1]
    h1 = _to_candles(df)
    if not h1 or time.time() - (h1[-1].time + HOUR) > HOUR:
        return None  # no fresh candle (the weekend, or Yahoo lags)
    dd = _fetch(symbol, "1d", DAILY_PERIOD)
    if dd is None:
        return None
    h4 = _to_candles(group_4h(df))
    since = h1[max(0, len(h1) - CONFIRM_BARS - 8)].time  # in candles, as a turn on Friday is confirmed on Monday
    found = [f for f in find_setups(h1, h4, _to_candles(dd), since) if f.confirm.idx == len(h1) - 1]
    return setup_from(symbol, name, df, h1, h4, found[-1], daily_candles(df)) if found else None


def scan() -> list[DailyZoneSetup]:
    results: list[DailyZoneSetup] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate, sym, name) for sym, name in UNIVERSE]
        for fut in as_completed(futures):
            if (result := fut.result()) is not None:
                results.append(result)
    results.sort(key=lambda s: (not s.strong, -s.touch_count))
    return results


def history_setups(symbol: str, name: Optional[str], df: pd.DataFrame, dd: pd.DataFrame) -> list[DailyZoneSetup]:
    """Every setup the live run would have sent on these 1h bars (df) and daily bars (dd), oldest first."""
    h1 = _to_candles(df)
    h4 = _to_candles(group_4h(df))
    days = pd.Series([_forex_day(ts) for ts in df.index], index=df.index)
    out = []
    for f in find_setups(h1, h4, _to_candles(dd)):
        day = _forex_day(pd.Timestamp(h1[f.confirm.idx].time, unit="s", tz="UTC"))
        if s := setup_from(symbol, name, df, h1, h4, f, daily_candles(df[days < day])):
            s.after = alert_chart.rest_of_day(h1, f.confirm.idx)
            out.append(s)
    return out


def format_message(s: DailyZoneSetup, example: bool = False) -> str:
    text = telegram.alert_caption(s.symbol, s.name, s.direction == "up", STRONG_TAG if s.strong else "")
    if example:
        when = datetime.fromisoformat(s.time) + timedelta(seconds=HOUR)
        text += f"\n🕘 דוגמה מההיסטוריה · {when.astimezone(ZoneInfo(settings.alert_timezone)):%d/%m %H:%M}"
    return text


def render(s: DailyZoneSetup) -> bytes:
    """The 4h chart (the daily zone, the turn framed) beside the 1h one (the daily zone,
    the 4h turn's hours framed, the confirmation and the trade)."""
    zone = (s.zone_bottom, s.zone_top, None)
    title = s.name or s.symbol
    turn = int(datetime.fromisoformat(s.turn_time).timestamp())
    before = [c for c in s.candles_4h if c.time < turn]
    turn_1h = [c for c in s.candles_1h if turn <= c.time < turn + 4 * HOUR]
    boxes_1h = [(_iso(turn_1h[0].time), _iso(turn_1h[-1].time))] if turn_1h else []
    retest = s.kind == "retest"
    return alert_chart.render_side_by_side(
        ((s.candles_4h, f"{title} · 4h", *zone), dict(
            direction=s.direction, breakout=s.turn_time, boxes=[(_iso(before[-1].time), s.turn_time)] if before else [],
            min_bars=SHOWN_4H, zone_colors=DAILY_COLORS, arrow_color=TURN_COLOR, box_color=TURN_COLOR, legend=LEGEND,
        )),
        ((s.candles_1h, f"{title} · 1h", *zone), dict(
            direction=s.direction, breakout=s.time, touches=[s.touch_time] if retest and s.touch_time else [],
            level=(s.level, s.swing_time) if retest else None, boxes=boxes_1h, min_bars=SHOWN_1H, plan=s.plan,
            zone_colors=DAILY_COLORS, arrow_color=CONFIRM_COLOR, box_color=TURN_COLOR, level_color=CONFIRM_COLOR,
            after=s.after, time_format="%d/%m\n%H:%M",  # the trade's box leaves the candles too narrow for one line
        )),
    )


def send_alert(s: DailyZoneSetup, example: bool = False) -> bool:
    """The 4h and 1h charts side by side with the message as their caption — or the
    message alone if the picture can't be drawn. Its trade is watched from then on
    (trade_tracker.py), unless it's an example from the past."""
    try:
        png = render(s)
    except Exception:
        logger.exception("Daily zone 4h alert: chart picture failed for %s, sending text only", s.symbol)
        png = None
    return trade_tracker.send_alert(png, format_message(s, example), BOT, s.key, s.symbol, s.name, None if example else s.plan)


def run_daily_zone_alerts(db: Session) -> int:
    """Scans the 1h candle that just closed and sends each new setup; returns how many went out."""
    with _run_lock:
        found = scan()
        if not telegram.is_configured():
            logger.info("Daily zone 4h alerts: Telegram not configured, %d found but not sent", len(found))
            return 0
        sent = send_new(db, found, send_alert, len(found))
        logger.info("Daily zone 4h alerts: %d setups in %d pairs, %d sent", len(found), len(UNIVERSE), sent)
        return sent


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    found = []
    for sym, name in UNIVERSE:
        df, dd = _fetch(sym, "1h", HISTORY_PERIOD), _fetch(sym, "1d", "2y")
        if df is not None and dd is not None:
            found += history_setups(sym, name, df, dd)
    found.sort(key=lambda s: s.time)
    print(f"{len(found)} setups in the past year ({sum(s.direction == 'down' for s in found)} shorts)")
    for s in found[-count:] if count else []:
        print(" ", s.time, s.symbol, s.direction, s.kind, "sent" if send_alert(s, example=True) else "FAILED")
        time.sleep(SEND_INTERVAL_SECONDS)
