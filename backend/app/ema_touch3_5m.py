"""The strategy MetaTrader 5 trades on its own — the user's (3/11/26), on 5-minute
candles of gold, the major pairs and the Nasdaq (MT5_STRATEGY_SYMBOLS), read from the
terminal itself (mt5_trader.py):

- over the 50 EMA, price came down to a zone and turned up from it twice, and now comes
  to it a third time: on that visit, a green candle closing over the body of the one
  before it (trade_plan.is_entry_candle) is a long. Under the EMA, the same at a zone of
  swing highs is a short. The touches as the 15m bot counts them (touch3_15m.find_setup),
  without its ranging check — not in the user's rules;
- the stop behind the zone and the visit's wicks, the target twice the risk (1:2);
- once the trade was 1:1 in profit, a zone against it touched a third time — a long's:
  swing highs, where price turned down — moves its stop to the entry: break even (the
  user's pick, over closing at once).

Every 5 minutes, a few seconds after each candle closes (scheduler.py). The alerts' bots
don't trade any more (MT5_BOTS blank) — the user's pick, for this strategy only."""
import logging
import time
from typing import Optional, Sequence

from . import mt5_trader
from .candles import Candle
from .config import settings
from .nasdaq_scalp import ema_series, recent_touches
from .touch3_15m import MAX_GAP_BARS, PRIOR_TOUCHES, find_setup, side_zones
from .trade_plan import TradePlan, behind, plan

logger = logging.getLogger(__name__)

BAR_SECONDS = 300
EMA_PERIOD = 50
REWARD_RISK = 2
# Zones from the last 16 hours' candles — as many candles as the 15m bot's window.
WINDOW_BARS = 192
# Enough candles before the window for the EMA to settle.
HISTORY_BARS = WINDOW_BARS + 5 * EMA_PERIOD
MAGIC = 30312  # its positions, apart from the alerts' (mt5_trader.MAGIC)
COMMENT = "BOT INVEST EMA 5m"
# The reversal against an open trade: a zone touched this many times.
AGAINST_TOUCHES = PRIOR_TOUCHES + 1

_last_bar: dict[str, int] = {}  # the broker's symbol -> its last candle looked at for an entry


def find_entry(window: Sequence[Candle], ema: float) -> Optional[TradePlan]:
    """The trade when the window's last candle is the entry on a zone's third touch, on
    the 50 EMA's side — ema is its value on that candle."""
    found = find_setup(window, ranging=False)
    if found is None:
        return None
    direction, zone, a = found
    if (window[-1].close > ema) != (direction == "up"):
        return None
    level = zone.top if direction == "down" else zone.bottom
    return plan(direction, window[-1].close, behind(window[a:], direction, level, zone.unit), REWARD_RISK)


def reached_one_r(window: Sequence[Candle], direction: str, entry: float, risk: float, opened: int) -> Optional[int]:
    """The first candle since the trade opened (at opened, the candles' clock) that got it
    1:1 in profit."""
    for i, c in enumerate(window):
        if c.time + BAR_SECONDS <= opened:
            continue
        if c.high >= entry + risk if direction == "up" else c.low <= entry - risk:
            return i
    return None


def turned_against(window: Sequence[Candle], direction: str, since: int) -> bool:
    """Whether a zone against the trade got its third touch (or a later one) from candle
    since on — a long's: swing highs, where price turned down; a short's: swing lows."""
    against = "down" if direction == "up" else "up"
    for side, zone in side_zones(window):
        if side != against or zone.touch_idx[-1] < since:
            continue
        if len(recent_touches(zone, zone.touch_idx[-1], MAX_GAP_BARS)) >= AGAINST_TOUCHES:
            return True
    return False


def manage(name: str, window: Sequence[Candle]) -> None:
    """Moves the symbol's open trade to break even, when it's due."""
    mt5 = mt5_trader.mt5
    for p in mt5.positions_get(symbol=name) or []:
        if p.magic != MAGIC or not p.tp:
            continue
        direction = "up" if p.type == mt5.POSITION_TYPE_BUY else "down"
        if p.sl and (p.sl >= p.price_open if direction == "up" else p.sl <= p.price_open):
            continue  # already at break even
        since = reached_one_r(window, direction, p.price_open, abs(p.tp - p.price_open) / REWARD_RISK, p.time)
        if since is not None and turned_against(window, direction, since):
            logger.info("EMA 5m: %s #%s was 1:1 up and turned at a zone against it — break even", name, p.ticket)
            mt5_trader.move_stop(p, p.price_open)


def server_now(names: Sequence[str]) -> Optional[float]:
    """Now on the broker's clock, which its candles' times are on — from the freshest quote."""
    ticks = [t.time for n in names if (t := mt5_trader.mt5.symbol_info_tick(n)) is not None and t.time]
    if not ticks:
        return None
    return time.time() + round((max(ticks) - time.time()) / 3600) * 3600


def closed_candles(name: str, now: float) -> list[Candle]:
    """The symbol's latest 5m candles that closed by now (the broker's clock)."""
    mt5 = mt5_trader.mt5
    rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_M5, 0, HISTORY_BARS)
    if rates is None:
        return []
    return [
        Candle(int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), int(r["tick_volume"]))
        for r in rates
        if r["time"] + BAR_SECONDS <= now
    ]


def _run_symbol(name: str, now: float) -> int:
    candles = closed_candles(name, now)
    if len(candles) < WINDOW_BARS:
        return 0
    window = candles[-WINDOW_BARS:]
    manage(name, window)
    last = window[-1]
    # Each candle once, and only the one that just closed (not after a market break).
    if _last_bar.get(name) == last.time or now - (last.time + BAR_SECONDS) > BAR_SECONDS:
        return 0
    _last_bar[name] = last.time
    trade = find_entry(window, ema_series(candles, EMA_PERIOD)[-1])
    if trade is None:
        return 0
    logger.info("EMA 5m: %s %s at %s, stop %s, target %s", "long" if trade.direction == "up" else "short", name, trade.entry, trade.stop, trade.target)
    return int(mt5_trader.place(name, trade, MAGIC, COMMENT) is not None)


def run_strategy() -> int:
    """Looks at each symbol's candle that just closed and its open trade; returns how many
    trades it opened."""
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
                logger.exception("EMA 5m: %s failed", name)
    if opened:
        logger.info("EMA 5m: %d trades opened", opened)
    return opened
