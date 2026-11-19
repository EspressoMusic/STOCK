"""The strategy MetaTrader 5 trades on its own since 11/11/26 — the user's ask: a grid for a
ranging market ("רשת דשדוש", like their old Bitcoin grid bot) on the Nasdaq (NAS100), in
place of the strong zones (strong_zone_1m.py, its code kept). Both ways (the user's pick):

- the range: the high and low of the last RANGE_DAYS daily candles closed, in STEPS steps;
- under the middle, a buy level each step: a 1m candle reaching one on the way down opens a
  long at the market, its target the level one step up;
- over the middle, a sell level each step: reaching one on the way up opens a short, its
  target one step down;
- the stop STOP_STEPS steps from the entry, away from the target (since 15/11/26, the user's
  pick: it was one step past the range, 5 steps off for the levels by the middle);
- one open trade a level; a new day's grid leaves the older trades to their stops and
  targets;
- the size: a whole side's trades stopped together lose SIDE_RISK_PERCENT of the balance —
  at least the symbol's smallest lot (the user: "if needed, a bigger lot").

The user switches it off by hand when a trend starts: MT5's "Algo Trading" button, and no
new trade opens (those open keep their stop and target at the broker).

On Yahoo's 1h candles (NQ=F, the 2 years to 11/11/26, with costs and a rough swap): 13.6
trades a week, 67% won, -0.76% a week, a 55% drawdown, the worst week -14% — under the 0.5%
a week other strategies were held to; the user chose to try it on the demo anyway, stopping
it in trends. The 2-step stop, rerun 15/11/26 with 2 points a trade: -0.51% a week, 66% won, a
51% drawdown — the old stop, rerun the same way, -0.40% a week, 70% won, 50%."""
import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from . import mt5_trader
from .candles import Candle
from .config import settings
from .ema_touch3_5m import server_now
from .trade_plan import TradePlan

logger = logging.getLogger(__name__)

SYMBOLS = ("NAS100",)
RANGE_DAYS = 10
STEPS = 10  # the range's steps: a level at each, the middle one neither side's
STOP_STEPS = 2  # each trade's stop, from its entry
SIDE_RISK_PERCENT = 5.0  # of the balance, lost when all of a side's trades are stopped
BAR_SECONDS = 60
DAY_SECONDS = 86400
MAGIC = 30314  # its positions, apart from the other strategies' (strong_zone_1m.MAGIC...)
COMMENT = "BOT INVEST Grid"

_last_bar: dict[str, int] = {}  # the broker's symbol -> its last candle looked at


@dataclass(frozen=True)
class Grid:
    low: float
    high: float

    @property
    def step(self) -> float:
        return (self.high - self.low) / STEPS

    @property
    def side_steps(self) -> int:
        """The steps a whole side's trades lose stopped together: STOP_STEPS each."""
        return STEPS // 2 * STOP_STEPS

    def levels(self, direction: str) -> list[float]:
        """The levels a trade opens at this way: the buys under the middle, the sells over it."""
        half = STEPS // 2
        steps = range(half) if direction == "up" else range(half + 1, STEPS + 1)
        return [self.low + i * self.step for i in steps]

    def plan(self, direction: str, level: float) -> TradePlan:
        """The level's trade: the target one step toward the middle, the stop STOP_STEPS
        steps the other way."""
        stop = STOP_STEPS * self.step
        if direction == "up":
            return TradePlan("up", level, level - stop, level + self.step)
        return TradePlan("down", level, level + stop, level - self.step)


def grid_of(days: Sequence[Candle]) -> Optional[Grid]:
    """The grid on the last RANGE_DAYS daily candles' high and low."""
    days = days[-RANGE_DAYS:]
    if len(days) < RANGE_DAYS:
        return None
    high, low = max(c.high for c in days), min(c.low for c in days)
    return Grid(low, high) if high > low else None


def crossed(grid: Grid, before: float, candle: Candle) -> list[tuple[str, float]]:
    """(direction, level) of each level the candle reached from the price before it: a buy
    level on the way down, a sell level on the way up."""
    buys = [("up", lv) for lv in grid.levels("up") if candle.low <= lv < before]
    sells = [("down", lv) for lv in grid.levels("down") if before < lv <= candle.high]
    return buys + sells


def taken(grid: Grid, plan: TradePlan, positions: Sequence) -> bool:
    """Whether one of our open trades this way has the level's target — within half a step,
    an older day's grid's level."""
    buy = plan.direction == "up"
    return any((p.type == 0) == buy and abs(p.tp - plan.target) < grid.step / 2 for p in positions)


def _open(name: str, grid: Grid, plan: TradePlan, positions: Sequence, balance: float) -> bool:
    """The level's trade at the market; whether it opened. Connected, with lock held."""
    mt5 = mt5_trader.mt5
    if taken(grid, plan, positions):
        logger.info("Grid: %s %s at %s already open", name, plan.direction, plan.entry)
        return False
    info, tick = mt5.symbol_info(name), mt5_trader.quote(name)
    if info is None or tick is None:
        logger.warning("Grid: no price for %s: %s", name, mt5.last_error())
        return False
    if not mt5_trader.levels_ok(plan.direction, tick.bid, tick.ask, plan.stop, plan.target):
        logger.info("Grid: %s at %s/%s, already past the stop %s or the target %s — skipped", name, tick.bid, tick.ask, plan.stop, plan.target)
        return False
    buy = plan.direction == "up"
    price = tick.ask if buy else tick.bid
    side_loss = grid.side_steps * grid.step
    loss = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, name, 1.0, price, price - side_loss if buy else price + side_loss)
    if not loss:
        logger.warning("Grid: can't work out %s's loss: %s", name, mt5.last_error())
        return False
    risk = balance * SIDE_RISK_PERCENT / 100
    lots = mt5_trader.lots_for_risk(risk, abs(loss), info.volume_step, info.volume_min, info.volume_max)
    if lots <= 0:
        lots = info.volume_min
        logger.warning("Grid: %s the smallest lot, %s, risks %.1f%% a side, over %s%%", name, lots, abs(loss) * lots / balance * 100, SIDE_RISK_PERCENT)
    sl, tp = round(plan.stop, info.digits), round(plan.target, info.digits)
    if settings.mt5_mode != "trade":
        logger.info("Grid PAPER: would %s %s %.2f lots @%s sl=%s tp=%s", "BUY" if buy else "SELL", name, lots, price, sl, tp)
        return True
    return mt5_trader.market_order(name, info, buy, price, lots, sl, tp, MAGIC, COMMENT) is not None


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


def _run_symbol(name: str, now: float) -> int:
    mt5 = mt5_trader.mt5
    minutes = _closed(name, mt5.TIMEFRAME_M1, BAR_SECONDS, 3, now)
    if len(minutes) < 2:
        return 0
    last = minutes[-1]
    # Each candle once, and only the one that just closed (not after a market break).
    if _last_bar.get(name) == last.time or now - (last.time + BAR_SECONDS) > BAR_SECONDS:
        return 0
    _last_bar[name] = last.time
    grid = grid_of(_closed(name, mt5.TIMEFRAME_D1, DAY_SECONDS, RANGE_DAYS + 2, now))
    if grid is None:
        return 0
    hits = crossed(grid, minutes[-2].close, last)
    acc = mt5.account_info()
    if not hits or mt5_trader.real_blocked(acc):
        return 0
    opened = 0
    for direction, level in hits:
        plan = grid.plan(direction, level)
        logger.info("Grid: %s reached %s (%s-%s, step %s): %s, stop %s, target %s", name, level, grid.low, grid.high, grid.step, "long" if direction == "up" else "short", plan.stop, plan.target)
        positions = [p for p in mt5.positions_get(symbol=name) or [] if p.magic == MAGIC]
        opened += _open(name, grid, plan, positions, acc.balance)
    return opened


def run_strategy() -> int:
    """Looks at each symbol's candle that just closed; returns how many trades it opened."""
    if settings.mt5_mode not in ("paper", "trade") or mt5_trader.mt5 is None:
        return 0
    opened = 0
    with mt5_trader.lock:
        if not mt5_trader.connect():
            return 0
        names = [n for s in SYMBOLS if (n := mt5_trader.broker_symbol(s))]
        for n in names:
            mt5_trader.mt5.symbol_select(n, True)
        now = server_now(names)
        if now is None:
            return 0
        for name in names:
            try:
                opened += _run_symbol(name, now)
            except Exception:
                logger.exception("Grid: %s failed", name)
    if opened:
        logger.info("Grid: %d trades opened", opened)
    return opened
