"""The MetaTrader 5 trades that closed, each sent once to the Nasdaq bot — the user's ask
(3/11/26): every trade taken here (ema_touch3_5m.py, and the alerts' if they trade
again), once it hit its stop, its target or break even. A picture of its 5m candles from
the terminal, the trade's box as it was opened, and the caption: the result, the
symbol, long or short, entry and exit, the money made or lost.

Read from the terminal's deal history — the deal that closed the position, its opening
order's stop and target — every 5 minutes after the strategy's run (scheduler.py). The
first run sends the ones already closed in the last LOOKBACK_DAYS too."""
import html
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from . import alert_chart, ema_touch3_5m, mt5_trader, telegram
from .candles import Candle
from .config import settings
from .models import SentAlert
from .trade_plan import TradePlan
from .zone_touch_scan import send_new

logger = logging.getLogger(__name__)

BOT = "nasdaq"
MAGICS = (ema_touch3_5m.MAGIC, mt5_trader.MAGIC)
LOOKBACK_DAYS = 7
MAX_ALERTS = 20
BAR_SECONDS = ema_touch3_5m.BAR_SECONDS
LEAD_BARS = 100  # candles before the entry in the picture
MAX_FETCH_BARS = 5000
MAX_OFFSET = 14 * 3600  # the broker's clock from UTC, at most
DEAL_ENTRY_IN = 0
DEAL_REASON_SL, DEAL_REASON_TP = 4, 5
HEADS = {
    "target": "✅ טייק פרופיט",
    "stop": "❌ סטופ לוס",
    "breakeven": "➖ ברייק איבן",
    "closed": "🔒 נסגרה",
}
TITLES = {"target": "Target hit", "stop": "Stop hit", "breakeven": "Break even", "closed": "Closed"}


@dataclass
class ClosedTrade:
    position: int
    symbol: str  # the broker's
    direction: str  # "up" | "down"
    entry: float
    exit: float
    stop: Optional[float]  # as opened — None if its order is gone from the history
    target: Optional[float]
    opened: int  # the broker's clock
    closed: int
    reason: int
    profit: float  # with commission, swap and fees
    candles: list[Candle] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"mt5-closed:{self.position}"

    @property
    def outcome(self) -> str:
        return outcome(self.direction, self.entry, self.exit, self.stop, self.reason)


def outcome(direction: str, entry: float, exit: float, stop: Optional[float], reason: int) -> str:
    """"target", "stop", "breakeven" (the stop, moved to the entry: closed nearer the entry
    than half the way to the stop it opened with) or "closed" (by hand)."""
    if reason == DEAL_REASON_TP:
        return "target"
    if reason != DEAL_REASON_SL:
        return "closed"
    if stop is not None and abs(exit - entry) < abs(stop - entry) / 2:
        return "breakeven"
    return "stop"


def closed_trades(deals: Sequence, orders: dict[int, object]) -> list[ClosedTrade]:
    """The positions both opened and closed in deals (the terminal's, ours by magic),
    oldest close first; orders are the opening orders by ticket (= the position's id)."""
    by_position: dict[int, list] = {}
    for d in deals:
        if d.magic in MAGICS and d.position_id:
            by_position.setdefault(d.position_id, []).append(d)
    out = []
    for position, ds in by_position.items():
        opening = next((d for d in ds if d.entry == DEAL_ENTRY_IN), None)
        closing = [d for d in ds if d.entry != DEAL_ENTRY_IN]
        if opening is None or not closing:
            continue
        last = max(closing, key=lambda d: d.time)
        order = orders.get(position)
        out.append(ClosedTrade(
            position=position, symbol=opening.symbol, direction="up" if opening.type == 0 else "down",
            entry=opening.price, exit=last.price,
            stop=(order.sl or None) if order else None, target=(order.tp or None) if order else None,
            opened=opening.time, closed=last.time, reason=last.reason,
            profit=sum(d.profit + d.commission + d.swap + d.fee for d in ds),
        ))
    return sorted(out, key=lambda t: t.closed)


def format_message(t: ClosedTrade) -> str:
    side = "לונג" if t.direction == "up" else "שורט"
    money = f"רווח {t.profit:.2f}$" if t.profit >= 0 else f"הפסד {-t.profit:.2f}$"
    return (
        f"{HEADS[t.outcome]} · <b>{html.escape(t.symbol)}</b> · {side}\n"
        f"כניסה {telegram.price(t.entry)} · יציאה {telegram.price(t.exit)} · {money}"
    )


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def picture(t: ClosedTrade) -> Optional[bytes]:
    """The 5m candles up to the one it closed in, the trade's box from the one it opened in."""
    opened, closed = t.opened - t.opened % BAR_SECONDS, t.closed - t.closed % BAR_SECONDS
    candles = [c for c in t.candles if c.time <= closed]
    if not candles or candles[-1].time != closed or not any(c.time == opened for c in candles):
        return None
    plan = TradePlan(t.direction, t.entry, t.stop, t.target) if t.stop and t.target else None
    return alert_chart.render(
        candles, f"{t.symbol} · 5m · {TITLES[t.outcome]}", None, None, None, direction=t.direction,
        plan=plan, plan_from=_iso(opened), closed=t.outcome, closed_at=_iso(closed),
    )


def send(t: ClosedTrade) -> bool:
    token, chat = telegram.bot_chat(BOT)
    try:
        png = picture(t)
    except Exception:
        logger.exception("MT5 closed: picture failed for %s, sending text only", t.key)
        png = None
    if png is None:
        return telegram.send_message(format_message(t), chat_id=chat, bot_token=token)
    return telegram.send_photo(png, format_message(t), chat_id=chat, bot_token=token)


def _candles(name: str, since: int, offset: int) -> list[Candle]:
    """The symbol's 5m candles from LEAD_BARS before since on, on the real clock (UTC)."""
    mt5 = mt5_trader.mt5
    count = min(MAX_FETCH_BARS, int((datetime.now(timezone.utc).timestamp() + offset - since) / BAR_SECONDS) + LEAD_BARS + 2)
    rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_M5, 0, count)
    if rates is None:
        return []
    return [
        Candle(int(r["time"]) - offset, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), int(r["tick_volume"]))
        for r in rates
    ]


def report_closed(db: Session) -> int:
    """Sends the trades closed since the last run (and the last LOOKBACK_DAYS) to the
    Nasdaq bot; returns how many went out."""
    if mt5_trader.mt5 is None or settings.mt5_mode != "trade" or not settings.telegram_nasdaq_bot_token:
        return 0
    with mt5_trader.lock:
        if not mt5_trader.connect():
            return 0
        mt5 = mt5_trader.mt5
        now = datetime.now()
        deals = mt5.history_deals_get(now - timedelta(days=LOOKBACK_DAYS), now + timedelta(days=1)) or []
        orders = {o.ticket: o for o in mt5.history_orders_get(now - timedelta(days=LOOKBACK_DAYS + 1), now + timedelta(days=1)) or []}
        trades = closed_trades(deals, orders)
        already = {k for (k,) in db.query(SentAlert.key).filter(SentAlert.key.in_([t.key for t in trades]))}
        trades = [t for t in trades if t.key not in already][:MAX_ALERTS]
        if not trades:
            return 0
        # The broker's clock from its freshest quote, as the strategy reads it.
        # A weekend's stale quotes give no clock: then the broker's times as they are.
        server = ema_touch3_5m.server_now(sorted({t.symbol for t in trades}))
        offset = round(server - datetime.now(timezone.utc).timestamp()) if server else 0
        offset = offset if abs(offset) <= MAX_OFFSET else 0
        for t in trades:
            t.candles = _candles(t.symbol, t.opened, offset)
            t.opened, t.closed = t.opened - offset, t.closed - offset
    sent = send_new(db, trades, send, MAX_ALERTS)
    if sent:
        logger.info("MT5 closed: %d trades sent to the Nasdaq bot", sent)
    return sent
