"""The strategy MetaTrader 5 trades on its own since 4/11/26 — the user's: strong zones on
EUR/USD, gold and the Nasdaq (MT5_STRATEGY_SYMBOLS), entered on 1-minute candles read from
the terminal itself (mt5_trader.py):

- a strong zone: price turned at it 3 times or more on the daily chart
  (daily_zone_4h.daily_zones, the forex bot's "strong place");
- a visit: price closed at least a typical daily candle away from the zone (LEAVE), then
  came back to it. From above it's support, a long; from below, resistance, a short. One
  trade a visit;
- the entry (the user's pick, over entering at the touch): within VISIT_BARS candles of
  the one that reached the zone, the first in the trade's colour closing past the body of
  the one before it (trade_plan.is_entry_candle). A close past the zone's far side ends
  the visit;
- the stop behind the visit's wicks, the target three times the risk (the user's 1:3),
  with no stop moves.

Every minute, a few seconds after each candle closes (scheduler.py). On the terminal's
ticks to 4/11/26 (EUR/USD and gold ~31 weeks, the Nasdaq ~24), with the real bid and ask
and commission, before swaps: 41 trades, 1.3 a week, 24% won, -0.12% a week at 1% risk —
too few trades to tell, under the 0.5% a week other strategies were held to; the user
chose to start with it on the demo."""
import logging
from typing import Optional, Sequence

from . import mt5_trader
from .candles import Candle
from .config import settings
from .daily_zone_4h import DAILY_WINDOW, daily_zones
from .ema_touch3_5m import server_now
from .trade_plan import TradePlan, behind, is_entry_candle, plan
from .zones import LEAVE, Zone, typical_candle

logger = logging.getLogger(__name__)

BAR_SECONDS = 60
DAY_SECONDS = 86400
REWARD_RISK = 3
VISIT_BARS = 15
# How far back the close away from the zone is looked for: three days of minutes.
HISTORY_BARS = 3 * 1440
# The daily candles fetched: the zones' window, and a week more for reading a trade's zone
# back when reporting it (mt5_closed.LOOKBACK_DAYS).
DAILY_BARS = DAILY_WINDOW + 7
TYPICAL_BARS = 120  # the minutes the stop's buffer is measured on
MAGIC = 30313  # its positions, apart from the other strategies' (mt5_trader.MAGIC, ema_touch3_5m.MAGIC)
COMMENT = "BOT INVEST Zone 1m"

_last_bar: dict[str, int] = {}  # the broker's symbol -> its last candle looked at for an entry


def visit_start(candles: Sequence[Candle], zone: Zone) -> Optional[tuple[str, int]]:
    """(direction, the visit's first candle): the first candle to reach the zone after the
    last close LEAVE typical daily candles away from it — before the last candle, which
    may be the entry leaving it."""
    leave = LEAVE * zone.unit
    last = len(candles) - 1
    away = next((i for i in range(last - 1, -1, -1) if candles[i].close > zone.top + leave or candles[i].close < zone.bottom - leave), None)
    if away is None:
        return None
    above = candles[away].close > zone.top
    for i in range(away + 1, last + 1):
        if candles[i].low <= zone.top if above else candles[i].high >= zone.bottom:
            return ("up" if above else "down"), i
    return None


def _first_entry(candles: Sequence[Candle], zone: Zone, direction: str, start: int) -> Optional[int]:
    """The visit's entry candle, None if it closed past the zone's far side before one."""
    for j in range(start, len(candles)):
        if candles[j].close < zone.bottom if direction == "up" else candles[j].close > zone.top:
            return None
        if is_entry_candle(candles[j - 1], candles[j], direction):
            return j
    return None


def find_setup(candles: Sequence[Candle], zones: Sequence[Zone]) -> Optional[tuple[str, Zone, int]]:
    """(direction, zone, the visit's first candle) when the last candle is the entry on a
    visit to one of the zones, the strongest first."""
    last = len(candles) - 1
    for zone in zones:
        found = visit_start(candles, zone)
        if found is None:
            continue
        direction, start = found
        if last - start < VISIT_BARS and _first_entry(candles, zone, direction, start) == last:
            return direction, zone, start
    return None


def find_entry(candles: Sequence[Candle], zones: Sequence[Zone]) -> Optional[TradePlan]:
    """The trade when the last candle is an entry: in on its close, the stop behind the
    visit's wicks, 1:3."""
    found = find_setup(candles, zones)
    if found is None:
        return None
    direction, _, start = found
    close = candles[-1].close
    unit = typical_candle(candles[-TYPICAL_BARS:]) * close
    return plan(direction, close, behind(candles[start:], direction, close, unit), REWARD_RISK)


def _closed(name: str, timeframe: int, seconds: int, count: int, now: float) -> list[Candle]:
    """The symbol's latest candles of the timeframe that closed by now (the broker's clock)."""
    rates = mt5_trader.mt5.copy_rates_from_pos(name, timeframe, 0, count)
    if rates is None:
        return []
    return [
        Candle(int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), int(r["tick_volume"]))
        for r in rates
        if r["time"] + seconds <= now
    ]


def closed_candles(name: str, now: float) -> list[Candle]:
    return _closed(name, mt5_trader.mt5.TIMEFRAME_M1, BAR_SECONDS, HISTORY_BARS, now)


def zones_at(name: str, now: float) -> list[Zone]:
    """The symbol's strong zones as of now (the broker's clock), from the daily candles
    closed by then."""
    return daily_zones(_closed(name, mt5_trader.mt5.TIMEFRAME_D1, DAY_SECONDS, DAILY_BARS, now))


def _run_symbol(name: str, now: float) -> int:
    candles = closed_candles(name, now)
    if len(candles) < 2:
        return 0
    last = candles[-1]
    # Each candle once, and only the one that just closed (not after a market break).
    if _last_bar.get(name) == last.time or now - (last.time + BAR_SECONDS) > BAR_SECONDS:
        return 0
    _last_bar[name] = last.time
    trade = find_entry(candles, zones_at(name, now))
    if trade is None:
        return 0
    logger.info("Zone 1m: %s %s at %s, stop %s, target %s", "long" if trade.direction == "up" else "short", name, trade.entry, trade.stop, trade.target)
    return int(mt5_trader.place(name, trade, MAGIC, COMMENT, REWARD_RISK) is not None)


def run_strategy() -> int:
    """Looks at each symbol's candle that just closed; returns how many trades it opened."""
    if settings.mt5_mode not in ("paper", "trade") or mt5_trader.mt5 is None:
        return 0
    opened = 0
    with mt5_trader.lock:
        if not mt5_trader.connect():
            return 0
        names = [n for s in settings.mt5_strategy_symbols.split(",") if s.strip() and (n := mt5_trader.broker_symbol(s.strip()))]
        for n in names:
            mt5_trader.mt5.symbol_select(n, True)
        now = server_now(names)
        if now is None:
            return 0
        for name in names:
            try:
                opened += _run_symbol(name, now)
            except Exception:
                logger.exception("Zone 1m: %s failed", name)
    if opened:
        logger.info("Zone 1m: %d trades opened", opened)
    return opened
