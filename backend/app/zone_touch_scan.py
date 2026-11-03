"""Forex pairs and stocks whose 4h chart has a zone price turned at three or more times.

The trainer's scanner ("3 נגיעות") lists, on demand, the pairs whose last candle closed
inside such a zone — price still sitting on it. The alerts wait for the entry: price came
to the zone, then a candle closes the trade's way past the body of the one before it
(trade_plan.py), and the alert comes with its trade — the stop behind the zone, 1:3.
Pairs are checked after each 4h candle closes (scheduler.py) and sent from the main bot;
stocks from their own bot, in the same run as type 2 (tight_touch_scan.py), so the
stocks are fetched once. The user calls them "אסטרטגיה 4" and "אסטרטגיה 4 · מניות" (the
1h retests are "אסטרטגיה 1"). The pairs' alerts are off since 31/10/26 (code kept): the
main bot sends daily_zone_4h.py's instead.

Zones are the chart's own (zones.py, what the trainer's automatic marking draws), so a
symbol shows the same zone when opened on the 4h chart. The trade's direction is the
side price came from: up into the zone from below, it's resistance — a trade down; down
into it from above, support — a trade up. Only when the daily candle agrees with that
direction (daily_candle.py), and not while price is ranging between this zone and an as
strong one close by (is_ranging)."""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from sqlalchemy.orm import Session

from . import alert_chart, telegram, trade_tracker
from .candles import Candle, _to_candles, get_4h_dataframe
from .config import settings
from .daily_candle import confirms, fetch_daily_candles
from .models import SentAlert
from .scanner import NASDAQ_EXCHANGES
from .technical_scan import (
    ALERT_FOREX,
    FOREX_PAIRS,
    TRIPLE_TOUCH_EXTRA_FOREX,
    _get_stock_universe,
    _round_sig,
    _stock_fetch_limiter,
)
from .trade_plan import TradePlan, zone_entry, zone_plan
from .zones import RETEST_REACH, Zone, detect_zones, typical_candle

logger = logging.getLogger(__name__)

FOREX = "forex"
STOCKS = "stocks"
FOREX_UNIVERSE = FOREX_PAIRS + TRIPLE_TOUCH_EXTRA_FOREX
# Yahoo's codes for Nasdaq's three tiers, NYSE, NYSE American and OTC (pink sheets) —
# where most penny stocks trade.
STOCK_EXCHANGES = [*NASDAQ_EXCHANGES, "NYQ", "ASE", "PNK"]
# 1h bars → ~250 four-hour candles: 6 a day for a pair, 2 a session for a stock.
HISTORY_PERIOD = {FOREX: "2mo", STOCKS: "6mo"}
# Zones come from the last this-many 4h candles (~3 weeks of a pair, ~10 of a stock), so
# every touch is recent — not a level price last turned months ago.
WINDOW_BARS = 100
MIN_TOUCHES = 3
# The last close may be this many typical candles past the zone's edge and still count
# as "in" it — zones are thin, and a close a hair outside is still sitting on the level.
# For the alerts, a candle reaching this near it came to it.
IN_ZONE_REACH = RETEST_REACH
# Ranging: a zone with at least as many touches, ahead in the trade's direction and its
# middle within this share of price from the last close. The user's pick (27/10/26): a
# far zone with more touches leaves room for the trade, and a near one with fewer is
# too weak to stop it — only both together (USD/JPY: ×8 up into ×9, 0.4% away) is out.
RANGE_DISTANCE = {FOREX: 0.01, STOCKS: 0.03}
# A stock whose typical 4h candle is smaller than this share of its price barely moves
# (a pending buyout, a SPAC at its trust value) and sits "in" a many-touch zone every
# day. Stricter than "שילה"'s 0.4%: those stocks came in at 0.42-0.47%, while a quiet
# big cap like KO is ~0.85%.
MIN_CANDLE_PCT = 0.006
# Telegram gets only the top of each run's list (most touches first) — the user asked for
# 10 while judging whether the scan is any good; the full ~130 stocks a day was too much.
MAX_ALERTS = 10
SEND_INTERVAL_SECONDS = 1.1
BAR_SECONDS = 4 * 3600


@dataclass
class ZoneTouchSetup:
    symbol: str
    name: Optional[str]
    direction: str  # "down" = came up into the zone from below (resistance) | "up" = from above
    price: float
    zone_bottom: float
    zone_top: float
    touch_count: int
    touch_times: list[str]  # the swing candle of each touch, oldest first
    distance_pct: float  # last close's distance from the zone's middle, % of price
    candles: list[Candle] = field(default_factory=list, repr=False)  # for the alert's picture
    # An alert's: the entry candle (the last, ISO) and its trade; None in the trainer's list.
    time: Optional[str] = None
    plan: Optional[TradePlan] = None

    @property
    def key(self) -> str:
        return f"zone4h:{self.symbol}:{self.time}"


@dataclass
class ZoneTouchScanResult:
    scanned_at: str
    universe_count: int
    results: list[ZoneTouchSetup]
    sent: bool = False


_run_lock = threading.Lock()
_alert_lock = threading.Lock()
_last_result: Optional[ZoneTouchScanResult] = None  # the pairs' — what the trainer lists


def get_last_result() -> Optional[ZoneTouchScanResult]:
    return _last_result


def find_zone(candles: Sequence[Candle], zones: Sequence[Zone]) -> Optional[Zone]:
    """The strongest of the zones (strongest first) that the last close is inside."""
    last = candles[-1].close
    for zone in zones:
        reach = IN_ZONE_REACH * zone.unit
        if zone.bottom - reach <= last <= zone.top + reach:
            return zone
    return None


def trade_direction(candles: Sequence[Candle], zone: Zone) -> Optional[str]:
    """A trade "down" if the latest close outside the zone was below it, "up" if above."""
    for c in reversed(candles):
        if c.close < zone.bottom:
            return "down"
        if c.close > zone.top:
            return "up"
    return None


def is_ranging(
    candles: Sequence[Candle], zones: Sequence[Zone], zone: Zone, direction: str, max_distance: float
) -> bool:
    """Whether another of the zones, ahead of this one in the trade's direction and its
    middle within max_distance (share of price) of the last close, has as many touches."""
    last = candles[-1].close
    for other in zones:
        ahead = other.top < zone.bottom if direction == "down" else other.bottom > zone.top
        if (
            ahead
            and len(other.touch_idx) >= len(zone.touch_idx)
            and abs(other.mid - last) / last <= max_distance
        ):
            return True
    return False


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def alert_setup(
    symbol: str,
    name: Optional[str],
    candles: Sequence[Candle],
    daily: Callable[[], Optional[Sequence[Candle]]],
    market: str,
) -> Optional[ZoneTouchSetup]:
    """The alert on the last of these closed 4h candles, if it's the entry at a 3-touch
    zone (trade_plan.zone_entry, the strongest zone first). daily fetches the daily
    candles, only once the rest has passed."""
    zones = detect_zones(candles, min_touches=MIN_TOUCHES)
    for zone in zones:
        found = zone_entry(candles, zone.bottom, zone.top, IN_ZONE_REACH * zone.unit)
        if found:
            break
    else:
        return None
    direction, a = found
    if is_ranging(candles, zones, zone, direction, RANGE_DISTANCE[market]):
        return None
    days = daily()
    if not days or not confirms(days, direction):
        return None
    trade = zone_plan(candles, a, direction, zone.bottom, zone.top, zone.unit)
    if trade is None:
        return None
    last = candles[-1].close
    i = len(candles) - 1
    return ZoneTouchSetup(
        symbol=symbol,
        name=name,
        direction=direction,
        price=_round_sig(last),
        zone_bottom=_round_sig(zone.bottom),
        zone_top=_round_sig(zone.top),
        touch_count=len(zone.touch_idx),
        # The visit's first candle too, unless it's the entry candle — that one gets the arrow.
        touch_times=[_iso(candles[k].time) for k in sorted({*zone.touch_idx, *([a] if a < i else [])})],
        distance_pct=round(abs(last - zone.mid) / last * 100, 2),
        candles=list(candles),
        time=_iso(candles[i].time),
        plan=trade,
    )


def closed(candles: list[Candle], now: datetime) -> list[Candle]:
    """A pair's 4h candles without the one still in progress — Yahoo includes the hour
    still trading."""
    return candles if candles and candles[-1].time + BAR_SECONDS <= now.timestamp() else candles[:-1]


def _evaluate_alert(symbol: str, name: Optional[str], now: datetime) -> Optional[ZoneTouchSetup]:
    df = get_4h_dataframe(symbol, period=HISTORY_PERIOD[FOREX])
    if df is None or df.empty:
        return None
    candles = closed(_to_candles(df), now)[-WINDOW_BARS:]
    if len(candles) < WINDOW_BARS // 2:
        return None
    return alert_setup(symbol, name, candles, lambda: fetch_daily_candles(symbol), FOREX)


def _evaluate(symbol: str, name: Optional[str], market: str) -> Optional[ZoneTouchSetup]:
    stock = market == STOCKS
    if stock:
        _stock_fetch_limiter.wait()
    df = get_4h_dataframe(symbol, period=HISTORY_PERIOD[market])
    if df is None or df.empty:
        return None
    candles = _to_candles(df)[-WINDOW_BARS:]
    if len(candles) < WINDOW_BARS // 2:
        return None
    if stock and typical_candle(candles) < MIN_CANDLE_PCT:
        return None

    zones = detect_zones(candles, min_touches=MIN_TOUCHES)
    zone = find_zone(candles, zones)
    if zone is None:
        return None
    direction = trade_direction(candles, zone)
    if direction is None or is_ranging(candles, zones, zone, direction, RANGE_DISTANCE[market]):
        return None
    if stock:
        _stock_fetch_limiter.wait()
    daily = fetch_daily_candles(symbol)
    if not daily or not confirms(daily, direction):
        return None
    last = candles[-1].close
    return ZoneTouchSetup(
        symbol=symbol,
        # A pair goes by its name ("EUR/USD"); a stock by its ticker, as on its chart.
        name=None if stock else name,
        direction=direction,
        price=_round_sig(last),
        zone_bottom=_round_sig(zone.bottom),
        zone_top=_round_sig(zone.top),
        touch_count=len(zone.touch_idx),
        touch_times=[_iso(candles[i].time) for i in zone.touch_idx],
        distance_pct=round(abs(last - zone.mid) / last * 100, 2),
        candles=candles,
    )


def scan_zone_touches(market: str = FOREX) -> ZoneTouchScanResult:
    if market == STOCKS:
        universe = _get_stock_universe(
            settings.stock_zone_touch_universe_size,
            min_volume=settings.stock_zone_touch_min_volume,
            exchanges=STOCK_EXCHANGES,
        )
        if not universe:
            logger.warning("Zone touch scan: empty stock universe (screener unavailable?)")
    else:
        universe = FOREX_UNIVERSE
    results: list[ZoneTouchSetup] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate, sym, name, market) for sym, name in universe]
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                results.append(result)
    results.sort(key=lambda s: (-s.touch_count, s.distance_pct))
    return ZoneTouchScanResult(
        scanned_at=datetime.now(timezone.utc).isoformat(),
        universe_count=len(universe),
        results=results,
    )


def format_message(s: ZoneTouchSetup) -> str:
    return telegram.alert_caption(s.symbol, s.name, s.direction == "up")


def send_alert(s: ZoneTouchSetup, market: str = FOREX) -> bool:
    """The setup's chart picture — the zone, its touches, the entry candle's arrow and the
    trade — with its message as the caption; or the message alone if the picture can't
    be drawn. Pairs from the main bot, stocks from theirs; the trade is watched from then
    on (trade_tracker.py)."""
    try:
        png = alert_chart.render(
            s.candles, f"{s.name or s.symbol} · 4h", s.zone_bottom, s.zone_top, s.touch_times[0],
            direction=s.direction, breakout=s.time, touches=s.touch_times, plan=s.plan,
        )
    except Exception:
        logger.exception("Zone touch scan: chart picture failed for %s, sending text only", s.symbol)
        png = None
    bot = "stocks" if market == STOCKS else "main"
    return trade_tracker.send_alert(png, format_message(s), bot, s.key, s.symbol, s.name, s.plan)


def run_zone_touch_scan(market: str = FOREX) -> ZoneTouchScanResult:
    """The trainer's list — no Telegram. The pairs' result is cached for the trainer."""
    global _last_result
    with _run_lock:
        result = scan_zone_touches(market)
        logger.info(
            "Zone touch scan (%s): %d in a 3-touch 4h zone out of %d", market, len(result.results), result.universe_count
        )
        if market == FOREX:
            _last_result = result
        return result


def send_new(db: Session, setups: Sequence, send: Callable, max_alerts: int = MAX_ALERTS) -> int:
    """Sends the setups (anything with a .key and a .symbol) not alerted before, up to
    max_alerts; returns how many went out. One whose send fails isn't recorded, so the
    next run tries it again."""
    already = {k for (k,) in db.query(SentAlert.key).filter(SentAlert.key.in_([s.key for s in setups]))}
    sent = 0
    for s in [s for s in setups if s.key not in already][:max_alerts]:
        if sent:
            time.sleep(SEND_INTERVAL_SECONDS)
        if send(s):
            db.add(SentAlert(key=s.key, symbol=s.symbol))
            db.commit()
            sent += 1
    return sent


def run_zone_touch_alerts(db: Session, now: Optional[datetime] = None) -> int:
    """The pairs' alerts on the 4h candle that just closed; returns how many went out."""
    now = now or datetime.now(timezone.utc)
    with _alert_lock:
        found: list[ZoneTouchSetup] = []
        with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
            futures = [pool.submit(_evaluate_alert, sym, name, now) for sym, name in ALERT_FOREX]
            for fut in as_completed(futures):
                if (result := fut.result()) is not None:
                    found.append(result)
        found.sort(key=lambda s: -s.touch_count)
        if not telegram.is_configured():
            logger.info("Zone touch alerts: Telegram not configured, %d found but not sent", len(found))
            return 0
        sent = send_new(db, found, send_alert)
        logger.info("Zone touch alerts (forex): %d entries in %d pairs, %d sent", len(found), len(ALERT_FOREX), sent)
        return sent
