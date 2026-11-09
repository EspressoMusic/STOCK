"""The MetaTrader 5 trades that closed, each sent once to the Nasdaq bot — the user's ask
(3/11/26): every trade taken here (strong_zone_1m.py, ema_touch3_5m.py's still open, and
the alerts' if they trade again), once it hit its stop, its target or break even. A picture
of the candles its strategy trades on from the terminal, the setup it was opened on (the
zone, its touches and the entry candle — the user's ask, 3/11/26), the trade's box as it
was opened, and the caption: the result, the symbol, long or short, the strategy, entry and
exit, the money made or lost.

Read from the terminal's deal history — the deal that closed the position, its opening
order's stop and target — after each of the strategy's runs (scheduler.py). The first run
sends the ones already closed in the last LOOKBACK_DAYS too."""
import html
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Sequence

from sqlalchemy.orm import Session

from . import alert_chart, ema_touch3_5m, mt5_trader, strong_zone_1m, telegram
from .candles import Candle
from .config import settings
from .models import SentAlert
from .touch3_15m import find_setup
from .trade_plan import TradePlan
from .zone_touch_scan import send_new
from .zones import Zone

logger = logging.getLogger(__name__)

BOT = "nasdaq"
# The strategy's name in the messages, by magic. The alerts' trades are only the 1h bot's
# (MT5_BOTS=1h, the user's ask, 4/11/26) — another bot there needs telling apart here.
STRATEGIES = {strong_zone_1m.MAGIC: "אזורים חזקים + נר דקה", ema_touch3_5m.MAGIC: "EMA 50 + 3 נגיעות", mt5_trader.MAGIC: "פורקס המשך"}
MAGICS = tuple(STRATEGIES)
LOOKBACK_DAYS = 7
MAX_ALERTS = 20
BAR_SECONDS = ema_touch3_5m.BAR_SECONDS
WINDOW_BARS = ema_touch3_5m.WINDOW_BARS
LEAD_BARS = WINDOW_BARS + 2  # candles fetched before the entry: the strategy's window, for its setup
MAX_PICTURE_BARS = 400  # the whole setup and trade shown, unless longer than this
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
    magic: int
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
    zones: list[Zone] = field(default_factory=list)  # a strong zone's trade's, as it was opened

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
            position=position, magic=opening.magic, symbol=opening.symbol, direction="up" if opening.type == 0 else "down",
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
        f"אסטרטגיה: {STRATEGIES[t.magic]}\n"
        f"כניסה {telegram.price(t.entry)} · יציאה {telegram.price(t.exit)} · {money}"
    )


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def bar_seconds(magic: int) -> int:
    """The candles of a trade's picture: those its strategy trades on."""
    return strong_zone_1m.BAR_SECONDS if magic == strong_zone_1m.MAGIC else BAR_SECONDS


def setup_marks(t, candles: Sequence[Candle]) -> dict:
    """The setup t (a ClosedTrade, or mt5_opened's OpenedTrade) was opened on, as the
    strategy read it: the zone, its touches and the entry candle (draw()'s arguments) —
    from the strategy's window of candles closed when it opened, read again. No zone for
    the alerts' trades, or when those candles don't make the setup any more (the broker's
    history changed)."""
    marks = dict(zone_bottom=None, zone_top=None, zone_start=None)
    closed = [c for c in candles if c.time + bar_seconds(t.magic) <= t.opened]
    strong = t.magic == strong_zone_1m.MAGIC
    if strong:
        window = closed[-strong_zone_1m.HISTORY_BARS:]
        found = strong_zone_1m.find_setup(window, t.zones) if window else None
    elif t.magic == ema_touch3_5m.MAGIC and len(closed) >= WINDOW_BARS:
        window = closed[-WINDOW_BARS:]
        found = find_setup(window, ranging=False)
    else:
        return marks
    if found is None or found[0] != t.direction:
        return marks
    _, zone, a = found
    last = len(window) - 1
    # A strong zone's touches are on the daily chart: here the visit's only, the zone across
    # the picture. The entry candle gets the arrow; a touch's dot there would cover it.
    touches = [_iso(window[k].time) for k in ([a] if strong else [*zone.touch_idx, a]) if k < last]
    return dict(
        zone_bottom=zone.bottom, zone_top=zone.top, zone_start=_iso(window[0].time) if strong else touches[0], touches=touches,
        breakout=_iso(window[last].time), zone_colors=alert_chart.GOLD,
    )


def picture(t: ClosedTrade) -> Optional[bytes]:
    """The strategy's candles up to the one it closed in, its setup, the trade's box from
    the one it opened in."""
    seconds = bar_seconds(t.magic)
    opened, closed = t.opened - t.opened % seconds, t.closed - t.closed % seconds
    candles = [c for c in t.candles if c.time <= closed]
    if not candles or candles[-1].time != closed or not any(c.time == opened for c in candles):
        return None
    plan = TradePlan(t.direction, t.entry, t.stop, t.target) if t.stop and t.target else None
    return alert_chart.render(
        candles, f"{t.symbol} · {seconds // 60}m · {TITLES[t.outcome]}", direction=t.direction, plan=plan,
        plan_from=_iso(opened), closed=t.outcome, closed_at=_iso(closed), max_bars=MAX_PICTURE_BARS,
        **setup_marks(t, candles),
    )


def send_with_picture(key: str, draw: Callable[[], Optional[bytes]], caption: str) -> bool:
    """The caption to the Nasdaq bot, under draw()'s picture — text only without one."""
    token, chat = telegram.bot_chat(BOT)
    try:
        png = draw()
    except Exception:
        logger.exception("MT5: picture failed for %s, sending text only", key)
        png = None
    if png is None:
        return telegram.send_message(caption, chat_id=chat, bot_token=token)
    return telegram.send_photo(png, caption, chat_id=chat, bot_token=token)


def send(t: ClosedTrade) -> bool:
    return send_with_picture(t.key, lambda: picture(t), format_message(t))


def broker_offset(names: Sequence[str]) -> int:
    """The broker's clock from UTC, from its freshest quote, as the strategy reads it. A
    weekend's stale quotes give no clock: then 0, the broker's times as they are."""
    server = ema_touch3_5m.server_now(names)
    offset = round(server - datetime.now(timezone.utc).timestamp()) if server else 0
    return offset if abs(offset) <= MAX_OFFSET else 0


def candles_from(name: str, since: int, offset: int, magic: int) -> list[Candle]:
    """The symbol's candles for a trade of this magic, from its strategy's window before
    since on, on the real clock (UTC)."""
    mt5 = mt5_trader.mt5
    strong = magic == strong_zone_1m.MAGIC
    lead = strong_zone_1m.HISTORY_BARS + 2 if strong else LEAD_BARS
    count = min(MAX_FETCH_BARS, int((datetime.now(timezone.utc).timestamp() + offset - since) / bar_seconds(magic)) + lead + 2)
    rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_M1 if strong else mt5.TIMEFRAME_M5, 0, count)
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
        offset = broker_offset(sorted({t.symbol for t in trades}))
        for t in trades:
            t.candles = candles_from(t.symbol, t.opened, offset, t.magic)
            if t.magic == strong_zone_1m.MAGIC:
                t.zones = strong_zone_1m.zones_at(t.symbol, t.opened)
            t.opened, t.closed = t.opened - offset, t.closed - offset
    sent = send_new(db, trades, send, MAX_ALERTS)
    if sent:
        logger.info("MT5 closed: %d trades sent to the Nasdaq bot", sent)
    return sent
