"""Telegram alerts for forex pairs that fell out of a zone and came back up to it, on
15-minute candles — the user's ask (29/10/26), in place of the penny stocks bot (same
bot, TELEGRAM_PENNY_BOT_TOKEN). Of the two readings offered the user picked the short:
price ranged on a zone, broke down out of it and fell, then rallied straight back to the
zone — sold there on the entry candle (trade_plan.py: stop behind the zone, 1:3), when
the daily candle agrees (daily_candle.py). Then the mirror too: broke up out of a zone
and rose, then came back down to it — a long.

Zones and breakouts are the chart's own (zones.py). No ranging filter (is_ranging): the
move away itself leaves a zone right past it, and on the past 60 days it dropped two
setups in three without making the rest any better. Runs every 15 minutes, on the candle
closes (scheduler.py), and on demand; every setup is sent once, remembered in the
sent_alerts table. `python -m app.drop_return_scan N` sends the last N shorts and the
last N longs of the past 60 days, marked as examples — for the user to judge the
strategy by."""
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
from .candles import MAX_CANDLES, Candle, _to_candles
from .config import settings
from .daily_candle import _forex_day, confirms, daily_candles
from .models import SentAlert
from .retests import ZONE_MIN_TOUCHES
from .technical_scan import ALERT_FOREX, _round_sig
from .trade_plan import TradePlan, is_entry_candle, zone_entry, zone_plan
from .zones import RETEST_REACH, Zone, detect_zones, find_breakouts

logger = logging.getLogger(__name__)

TIMEFRAME = "15m"
BAR_SECONDS = 900
UNIVERSE = ALERT_FOREX  # the watchlist pairs, as the other forex bots
BOT = "penny"  # telegram.bot_chat — the penny stocks bot's token and chat
# Zones from the candles the chart's 15m view shows, so they match what it draws.
WINDOW_BARS = MAX_CANDLES
LIVE_PERIOD = "1mo"  # the window, plus the two weeks of daily candles daily_candles reads
HISTORY_PERIOD = "60d"  # Yahoo's limit for 15m bars
# The fall (the rise, after a breakout up): price got at least this many typical candles
# past the zone between the breakout and the return. 4 keeps a real move, not a poke past
# the zone (~4 shorts a day on the 20 pairs over the past 60 days).
MIN_MOVE = 4
# The return has to start within this many candles (a day) of the breakdown — coming
# back days later is just revisiting an old level.
MAX_RETURN_BARS = 96
# A setup is sent only while its candle closed at most this long ago — a 15m alert that
# comes late (the server was down, Yahoo lagged) is no use.
MAX_AGE_SECONDS = 2 * BAR_SECONDS
# Telegram allows about one message a second into the same chat.
SEND_INTERVAL_SECONDS = 1.1


@dataclass
class DropReturnSetup:
    symbol: str
    name: Optional[str]
    direction: str  # "down" = fell and came back up (short) | "up" = rose and came back down (long)
    zone_bottom: float
    zone_top: float
    zone_start_time: str  # the zone's first touch
    price: float
    breakout_time: str  # the candle that broke out of the zone
    return_time: str  # the return's first candle back at the zone
    time: str  # the entry candle's (ISO); the alert goes out when it closes
    key: str  # one alert per breakdown
    candles: list[Candle] = field(default_factory=list, repr=False)  # for the alert's picture
    plan: Optional[TradePlan] = None  # entered on the last candle
    after: list[Candle] = field(default_factory=list, repr=False)  # an example's: the rest of its day


@dataclass
class DropReturnScanResult:
    scanned_at: str
    universe_count: int
    results: list[DropReturnSetup]
    sent_count: int


_run_lock = threading.Lock()


def find_setup(candles: Sequence[Candle]) -> Optional[tuple[str, Zone, int, int]]:
    """(direction, zone, breakout candle, the return's first candle) when the last candle
    is the entry of a return (trade_plan.zone_entry): price broke out of the zone the way
    it now comes back from — down for a short, up for a long — then stayed clear of it and
    got MIN_MOVE typical candles past it, and this is its first visit back, within
    MAX_RETURN_BARS. The strongest zone first."""
    # zone_entry checks this too; first, as it's cheap and the zones aren't.
    if len(candles) < 3 or not any(is_entry_candle(candles[-2], candles[-1], d) for d in ("down", "up")):
        return None
    for zone in detect_zones(candles, min_touches=ZONE_MIN_TOUCHES):
        reach = RETEST_REACH * zone.unit
        found = zone_entry(candles, zone.bottom, zone.top, reach)
        if found is None:
            continue
        direction, a = found
        up = direction == "up"
        breakouts = [b for b in find_breakouts(candles, zone) if b.idx < a]
        if not breakouts or breakouts[-1].dir != direction:
            continue
        b = breakouts[-1].idx
        away = candles[b + 1 : a]
        if not away or a - b > MAX_RETURN_BARS:
            continue
        if any(c.low <= zone.top + reach if up else c.high >= zone.bottom - reach for c in away):
            continue
        move = max(c.high for c in candles[b:a]) - zone.top if up else zone.bottom - min(c.low for c in candles[b:a])
        if move < MIN_MOVE * zone.unit:
            continue
        return direction, zone, b, a
    return None


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def setup_from(
    symbol: str, name: Optional[str], candles: Sequence[Candle], daily: Sequence[Candle]
) -> Optional[DropReturnSetup]:
    """The alert on the last of these closed 15m candles; daily = the closed daily
    candles before its day."""
    found = find_setup(candles)
    if found is None or not confirms(daily, found[0]):
        return None
    direction, zone, b, a = found
    # The stop behind the zone and the return's wicks.
    trade = zone_plan(candles, a, direction, zone.bottom, zone.top, zone.unit)
    if trade is None:
        return None
    breakout_time = _iso(candles[b].time)
    return DropReturnSetup(
        symbol=symbol,
        name=name,
        direction=direction,
        zone_bottom=_round_sig(zone.bottom),
        zone_top=_round_sig(zone.top),
        zone_start_time=_iso(candles[zone.start_idx].time),
        price=_round_sig(candles[-1].close),
        breakout_time=breakout_time,
        return_time=_iso(candles[a].time),
        time=_iso(candles[-1].time),
        key=f"dropreturn:{TIMEFRAME}:{symbol}:{breakout_time}",
        candles=list(candles),
        plan=trade,
    )


def _fetch(symbol: str, period: str) -> Optional[pd.DataFrame]:
    try:
        df = yf.Ticker(symbol).history(interval=TIMEFRAME, period=period)
    except Exception:
        logger.warning("Drop-return scan: 15m history fetch failed for %s", symbol)
        return None
    return None if df is None or df.empty else df


def _evaluate(symbol: str, name: Optional[str]) -> Optional[DropReturnSetup]:
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


def scan_drop_returns() -> tuple[int, list[DropReturnSetup]]:
    results: list[DropReturnSetup] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate, sym, name) for sym, name in UNIVERSE]
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                results.append(result)
    return len(UNIVERSE), results


def history_setups(symbol: str, name: Optional[str], df: pd.DataFrame) -> list[DropReturnSetup]:
    """Every setup the live run would have sent on these 15m bars, oldest first."""
    candles = _to_candles(df)
    days = pd.Series([_forex_day(ts) for ts in df.index], index=df.index)
    out = []
    for i in range(WINDOW_BARS, len(candles)):
        window = candles[i + 1 - WINDOW_BARS : i + 1]
        if find_setup(window) is None:
            continue
        day = _forex_day(pd.Timestamp(window[-1].time, unit="s", tz="UTC"))
        s = setup_from(symbol, name, window, daily_candles(df[days < day]))
        if s:
            s.after = alert_chart.rest_of_day(candles, i)
            out.append(s)
    return out


def format_message(s: DropReturnSetup, example: bool = False) -> str:
    text = telegram.alert_caption(s.symbol, s.name, s.direction == "up")
    if example:
        when = datetime.fromisoformat(s.time) + timedelta(seconds=BAR_SECONDS)
        text += f"\n🕘 דוגמה מההיסטוריה · {when.astimezone(ZoneInfo(settings.alert_timezone)):%d/%m %H:%M}"
    return text


def send_alert(s: DropReturnSetup, example: bool = False) -> bool:
    """The chart picture with the message as its caption — or the message alone if the
    picture can't be drawn. Its trade is watched from then on (trade_tracker.py), unless
    it's an example from the past."""
    try:
        png = alert_chart.render(
            s.candles, f"{s.name or s.symbol} · {TIMEFRAME}", s.zone_bottom, s.zone_top, s.zone_start_time,
            direction=s.direction, breakout=s.breakout_time, touches=[s.return_time], plan=s.plan, after=s.after,
        )
    except Exception:
        logger.exception("Drop-return alert: chart picture failed for %s, sending text only", s.symbol)
        png = None
    return trade_tracker.send_alert(png, format_message(s, example), BOT, s.key, s.symbol, s.name, None if example else s.plan)


def run_drop_return_alerts(db: Session) -> DropReturnScanResult:
    """Scans, then sends every setup not alerted before. A setup whose send fails isn't
    recorded, so the next run tries it again."""
    with _run_lock:
        universe_count, results = scan_drop_returns()
        sent = 0
        if telegram.is_configured() and settings.telegram_penny_bot_token:
            already = {k for (k,) in db.query(SentAlert.key).filter(SentAlert.key.in_([s.key for s in results]))}
            for s in results:
                if s.key in already:
                    continue
                if sent:
                    time.sleep(SEND_INTERVAL_SECONDS)
                if send_alert(s):
                    db.add(SentAlert(key=s.key, symbol=s.symbol))
                    db.commit()
                    sent += 1
        else:
            logger.info("Drop-return scan: no TELEGRAM_PENNY_BOT_TOKEN, %d setups found but not sent", len(results))
        logger.info("Drop-return scan: %d setups in %d pairs, %d alerts sent", len(results), universe_count, sent)
        return DropReturnScanResult(
            scanned_at=datetime.now(timezone.utc).isoformat(),
            universe_count=universe_count,
            results=results,
            sent_count=sent,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    found = []
    for sym, name in UNIVERSE:
        df = _fetch(sym, HISTORY_PERIOD)
        if df is not None:
            found += history_setups(sym, name, df)
    found.sort(key=lambda s: s.time)
    for direction in ("down", "up"):
        mine = [s for s in found if s.direction == direction]
        print(f"{'Shorts' if direction == 'down' else 'Longs'}: {len(mine)} in the past 60 days")
        for s in mine[-count:] if count else []:
            print(" ", s.time, s.symbol, "sent" if send_alert(s, example=True) else "FAILED")
            time.sleep(SEND_INTERVAL_SECONDS)
