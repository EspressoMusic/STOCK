"""The MetaTrader 5 trades that opened, each sent once to the Nasdaq bot — the user's ask
(4/11/26), beside the ones that closed (mt5_closed.py). A picture of the candles its
strategy trades on from the terminal up to the entry candle, the setup it was opened on and
the trade's box, and the caption: the symbol, long or short, the strategy, entry, stop and
target.

Read from the terminal's open positions right after each strategy's run (scheduler.py), so
it goes out within seconds of the order. The first run sends the ones already open too."""
import html
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from . import alert_chart, mt5_trader, strong_zone_1m, telegram
from .candles import Candle
from .config import settings
from .models import SentAlert
from .mt5_closed import MAGICS, MAX_ALERTS, MAX_PICTURE_BARS, STRATEGIES, bar_seconds, broker_offset, candles_from, send_with_picture, setup_marks
from .trade_plan import TradePlan
from .zone_touch_scan import send_new
from .zones import Zone

logger = logging.getLogger(__name__)

_run_lock = threading.Lock()  # two strategies' runs, or the same trade would go out twice


@dataclass
class OpenedTrade:
    position: int
    magic: int
    symbol: str  # the broker's
    direction: str  # "up" | "down"
    entry: float
    stop: Optional[float]
    target: Optional[float]
    opened: int  # the broker's clock
    candles: list[Candle] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)  # a strong zone's trade's, as it was opened

    @property
    def key(self) -> str:
        return f"mt5-opened:{self.position}"


def opened_trades(positions: Sequence) -> list[OpenedTrade]:
    """The terminal's open positions, ours by magic, oldest first."""
    return sorted(
        (
            OpenedTrade(
                position=p.ticket, magic=p.magic, symbol=p.symbol, direction="up" if p.type == 0 else "down",
                entry=p.price_open, stop=p.sl or None, target=p.tp or None, opened=p.time,
            )
            for p in positions
            if p.magic in MAGICS
        ),
        key=lambda t: t.opened,
    )


def format_message(t: OpenedTrade) -> str:
    side = "לונג" if t.direction == "up" else "שורט"
    levels = (("כניסה", t.entry), ("סטופ", t.stop), ("יעד", t.target))
    return (
        f"🟢 נכנס לעסקה · <b>{html.escape(t.symbol)}</b> · {side}\n"
        f"אסטרטגיה: {STRATEGIES[t.magic]}\n"
        + " · ".join(f"{name} {telegram.price(value)}" for name, value in levels if value)
    )


def picture(t: OpenedTrade) -> Optional[bytes]:
    """The strategy's candles closed when it opened, its setup, the trade's box from the
    last one — the entry candle."""
    seconds = bar_seconds(t.magic)
    candles = [c for c in t.candles if c.time + seconds <= t.opened]
    if not candles:
        return None
    plan = TradePlan(t.direction, t.entry, t.stop, t.target) if t.stop and t.target else None
    return alert_chart.render(
        candles, f"{t.symbol} · {seconds // 60}m · Entry", direction=t.direction, plan=plan, max_bars=MAX_PICTURE_BARS,
        **setup_marks(t, candles),
    )


def send(t: OpenedTrade) -> bool:
    return send_with_picture(t.key, lambda: picture(t), format_message(t))


def report_opened(db: Session) -> int:
    """Sends the trades opened since the last run to the Nasdaq bot; returns how many went
    out."""
    if mt5_trader.mt5 is None or settings.mt5_mode != "trade" or not settings.telegram_nasdaq_bot_token:
        return 0
    with _run_lock:
        with mt5_trader.lock:
            if not mt5_trader.connect():
                return 0
            trades = opened_trades(mt5_trader.mt5.positions_get() or [])
            already = {k for (k,) in db.query(SentAlert.key).filter(SentAlert.key.in_([t.key for t in trades]))}
            trades = [t for t in trades if t.key not in already][:MAX_ALERTS]
            if not trades:
                return 0
            offset = broker_offset(sorted({t.symbol for t in trades}))
            for t in trades:
                t.candles = candles_from(t.symbol, t.opened, offset, t.magic)
                if t.magic == strong_zone_1m.MAGIC:
                    t.zones = strong_zone_1m.zones_at(t.symbol, t.opened)
                t.opened -= offset
        sent = send_new(db, trades, send, MAX_ALERTS)
    if sent:
        logger.info("MT5 opened: %d trades sent to the Nasdaq bot", sent)
    return sent
