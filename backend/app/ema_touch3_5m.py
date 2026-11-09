"""The strategy MetaTrader 5 trades on its own — the user's (3/11/26), on 5-minute
candles of gold, the major pairs and the Nasdaq (MT5_STRATEGY_SYMBOLS), read from the
terminal itself (mt5_trader.py):

- over the 50 EMA, price came down to a zone and turned up from it twice, and now comes
  to it a third time: on that visit, a green candle closing over the body of the one
  before it (trade_plan.is_entry_candle) is a long. Under the EMA, the same at a zone of
  swing highs is a short. The touches as the 15m bot counts them (touch3_15m.find_setup),
  without its ranging check — not in the user's rules;
- no long while a big red candle's fair value gap is open — price hasn't closed over it
  since; no short while a big green one's is (open_gap_against);
- no trade once a candle closed past the zone since its first touch — a long's: under
  it — not even by a little (closed_through; the 15m bot lets it stray half a candle);
- the stop behind the zone and the visit's wicks, the target twice the risk (1:2);
- once the trade was 1:1 in profit, a zone against it touched a third time — a long's:
  swing highs, where price turned down — moves its stop to the entry: break even (the
  user's pick, over closing at once);
- once it was 1.5:1 in profit, its stop moves to 1:1 — the user's (3/11/26). If price
  came back under 1:1 by then (the broker refuses that stop), to the entry.

Every 5 minutes, a few seconds after each candle closes (scheduler.py). Of the alerts' bots
only the 15m one trades too (MT5_BOTS=1h), its positions apart by their magic."""
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
# Once a trade was this many times its risk in profit, its stop locks this many.
LOCK_AT = 1.5
LOCK_R = 1
# A big candle: its body this many times the average of the candles before it.
BIG_BODY = 3
BODY_AVG_BARS = 20

_last_bar: dict[str, int] = {}  # the broker's symbol -> its last candle looked at for an entry


def open_gap_against(window: Sequence[Candle], direction: str) -> bool:
    """Whether a big candle against the trade — a long's: a big red one — left a fair value
    gap (the candle after it doesn't reach back to the one before it) that no candle since
    closed past — a long's: over its top. The user's (3/11/26)."""
    for i in range(BODY_AVG_BARS, len(window) - 1):
        before, big, after = window[i - 1], window[i], window[i + 1]
        avg = sum(abs(c.close - c.open) for c in window[i - BODY_AVG_BARS:i]) / BODY_AVG_BARS
        if abs(big.close - big.open) < BIG_BODY * avg:
            continue
        if direction == "up" and big.close < big.open and after.high < before.low:
            if all(c.close <= before.low for c in window[i + 2:]):
                return True
        elif direction == "down" and big.close > big.open and after.low > before.high:
            if all(c.close >= before.high for c in window[i + 2:]):
                return True
    return False


def closed_through(candles: Sequence[Candle], direction: str, bottom: float, top: float) -> bool:
    """Whether a candle closed past the zone, even by a little — a long's: under its
    bottom. The user's (3/11/26), after a USD/CHF long whose visit closed under it."""
    return any(c.close < bottom if direction == "up" else c.close > top for c in candles)


def find_entry(window: Sequence[Candle], ema: float) -> Optional[TradePlan]:
    """The trade when the window's last candle is the entry on a zone's third touch, on
    the 50 EMA's side — ema is its value on that candle — with no big candle's gap open
    against it."""
    found = find_setup(window, ranging=False)
    if found is None:
        return None
    direction, zone, a = found
    if (window[-1].close > ema) != (direction == "up"):
        return None
    if open_gap_against(window, direction):
        return None
    if closed_through(window[zone.start_idx:], direction, zone.bottom, zone.top):
        return None
    level = zone.top if direction == "down" else zone.bottom
    return plan(direction, window[-1].close, behind(window[a:], direction, level, zone.unit), REWARD_RISK)


def reached(window: Sequence[Candle], direction: str, entry: float, profit: float, opened: int) -> Optional[int]:
    """The first candle since the trade opened (at opened, the candles' clock) that got it
    profit (a price distance) in profit."""
    for i, c in enumerate(window):
        if c.time + BAR_SECONDS <= opened:
            continue
        if c.high >= entry + profit if direction == "up" else c.low <= entry - profit:
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
    """Moves the symbol's open trade's stop, when it's due: to 1:1 once it was 1.5:1 in
    profit, to break even on a turn against it."""
    mt5 = mt5_trader.mt5
    for p in mt5.positions_get(symbol=name) or []:
        if p.magic != MAGIC or not p.tp:
            continue
        direction = "up" if p.type == mt5.POSITION_TYPE_BUY else "down"
        sign = 1 if direction == "up" else -1
        risk = abs(p.tp - p.price_open) / REWARD_RISK
        lock = p.price_open + sign * LOCK_R * risk
        if p.sl and sign * (p.sl - lock) >= 0:
            continue  # already locked
        at_entry = bool(p.sl) and sign * (p.sl - p.price_open) >= 0
        if reached(window, direction, p.price_open, LOCK_AT * risk, p.time) is not None:
            logger.info("EMA 5m: %s #%s was 1.5:1 up — stop to 1:1", name, p.ticket)
            if not mt5_trader.move_stop(p, lock) and not at_entry:
                mt5_trader.move_stop(p, p.price_open)  # back under 1:1 already
            continue
        if at_entry:
            continue
        since = reached(window, direction, p.price_open, risk, p.time)
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
