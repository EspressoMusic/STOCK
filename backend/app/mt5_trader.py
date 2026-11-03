"""Opens the forex alerts' trades on MetaTrader 5 — the user's ask (3/11/26), with the
order code of their BOT MO project (botmo/live/mt5_bot.py's Broker).

The terminal must be installed, open and logged in on this computer: the MetaTrader5
package (Windows only) talks to it. Every alert of a bot in MT5_BOTS on a forex pair that
goes out with its trade (trade_tracker.send_alert) is a market order there, the alert's
stop and target with it — the broker closes it, nothing more to watch. Since the same
day the alerts' bots are off here (MT5_BOTS blank, the user's pick): the strategy that
trades is ema_touch3_5m.py's, through place and move_stop.

Safety, as in BOT MO:
  * MT5_MODE "off" (default) — nothing; "paper" — the order is only logged; "trade" —
    sent, and on a DEMO account only unless MT5_ALLOW_REAL;
  * the size from the risk: MT5_RISK_PERCENT of the balance lost at the stop;
  * one trade per pair — an alert on a pair already held is skipped;
  * a price already past the stop or the target — skipped."""
import logging
import math
import threading
import time
from typing import Optional

from .config import settings
from .trade_plan import TradePlan

try:
    import MetaTrader5 as mt5
except ImportError:  # not on Windows (Render), or not installed
    mt5 = None

logger = logging.getLogger(__name__)

MAGIC = 30311  # this app's orders, apart from BOT MO's (50505) and the ones by hand
DEVIATION = 30  # points the price may move between the quote and the fill
TICK_TRIES = 6  # half a second apart, for a symbol's first quote

lock = threading.Lock()  # every call to the terminal holds it
_connected = False
_symbols: dict[str, str] = {}  # ours (Yahoo's EURUSD=X, or XAUUSD) -> the broker's


def enabled_for(bot: str, symbol: str) -> bool:
    bots = {b.strip() for b in settings.mt5_bots.split(",") if b.strip()}
    return settings.mt5_mode in ("paper", "trade") and bot in bots and symbol.endswith("=X")


def lots_for_risk(risk: float, loss_per_lot: float, step: float, vmin: float, vmax: float) -> float:
    """The most lots, in the symbol's steps, that lose at most risk at the stop; 0 when even
    the smallest lot loses more."""
    if not (risk > 0 and loss_per_lot > 0 and step > 0):
        return 0.0
    lots = math.floor(risk / loss_per_lot / step + 1e-9) * step
    if lots < vmin:
        return 0.0
    return round(min(lots, vmax), 8)


def levels_ok(direction: str, bid: float, ask: float, stop: float, target: float) -> bool:
    """Whether the price is still between the stop and the target."""
    if direction == "up":
        return stop < bid and ask < target
    return target < bid and ask < stop


def connect() -> bool:
    """Connects to the terminal — again after it was closed; whether it's logged in."""
    global _connected
    if _connected and mt5.terminal_info() is not None and mt5.account_info() is not None:
        return True
    kw = {"path": settings.mt5_terminal_path} if settings.mt5_terminal_path else {}
    if not mt5.initialize(**kw):
        logger.warning("MT5: can't connect to the terminal (is it open and logged in?): %s", mt5.last_error())
        _connected = False
        return False
    acc = mt5.account_info()
    if acc is None:
        logger.warning("MT5: the terminal isn't logged in to an account")
        _connected = False
        return False
    _connected = True
    logger.info(
        "MT5 connected: %s #%s, %s, balance %.2f %s",
        acc.server, acc.login, "demo" if acc.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "REAL", acc.balance, acc.currency,
    )
    return True


def broker_symbol(symbol: str) -> Optional[str]:
    """Yahoo's EURUSD=X (or a plain XAUUSD) as the broker names it: EURUSD with
    MT5_SYMBOL_SUFFIX, else the first symbol starting EURUSD (EURUSD.m, EURUSDm...)."""
    if symbol not in _symbols:
        base = symbol.removesuffix("=X")
        name = base + settings.mt5_symbol_suffix
        if mt5.symbol_info(name) is None:
            found = mt5.symbols_get(group=f"{base}*") or []
            if not found:
                return None
            name = found[0].name
        _symbols[symbol] = name
    return _symbols[symbol]


def _tick(name: str):
    """The symbol's quote — a symbol just added to Market Watch has none for a moment."""
    for _ in range(TICK_TRIES):
        tick = mt5.symbol_info_tick(name)
        if tick is not None and tick.bid > 0 and tick.ask > 0:
            return tick
        time.sleep(0.5)
    return None


def _filling(info):
    fm = info.filling_mode
    return mt5.ORDER_FILLING_FOK if fm & 1 else (mt5.ORDER_FILLING_IOC if fm & 2 else mt5.ORDER_FILLING_RETURN)


def open_trade(bot: str, symbol: str, plan: TradePlan) -> Optional[int]:
    """The alert's trade as a market order: its ticket (0 on paper), None when it wasn't
    opened. Never raises — the alert went out whatever happens here."""
    if not enabled_for(bot, symbol):
        return None
    if mt5 is None:
        logger.warning("MT5: the MetaTrader5 package isn't installed (Windows only) — %s not traded", symbol)
        return None
    try:
        with lock:
            if not connect():
                return None
            name = broker_symbol(symbol)
            if name is None:
                logger.warning("MT5: no symbol for %s at %s", symbol, mt5.account_info().server)
                return None
            return place(name, plan, MAGIC, f"BOT INVEST {bot}")
    except Exception:
        logger.exception("MT5: opening %s failed", symbol)
        return None


def place(name: str, plan: TradePlan, magic: int = MAGIC, comment: str = "BOT INVEST") -> Optional[int]:
    """The trade as a market order on the broker's symbol name, under this magic number:
    its ticket (0 on paper), None when it wasn't opened. Connected, with lock held."""
    acc = mt5.account_info()
    if settings.mt5_mode == "trade" and acc.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO and not settings.mt5_allow_real:
        logger.warning("MT5: #%s is a REAL account — not traded (MT5_ALLOW_REAL=true allows it)", acc.login)
        return None
    mt5.symbol_select(name, True)
    if any(p.magic == magic for p in mt5.positions_get(symbol=name) or []):
        logger.info("MT5: %s already held — not traded again", name)
        return None
    info, tick = mt5.symbol_info(name), _tick(name)
    if info is None or tick is None:
        logger.warning("MT5: no price for %s: %s", name, mt5.last_error())
        return None
    if not levels_ok(plan.direction, tick.bid, tick.ask, plan.stop, plan.target):
        logger.info("MT5: %s at %s/%s, already past the stop %s or the target %s — skipped", name, tick.bid, tick.ask, plan.stop, plan.target)
        return None
    buy = plan.direction == "up"
    order_type = mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL
    price = tick.ask if buy else tick.bid
    loss = mt5.order_calc_profit(order_type, name, 1.0, price, plan.stop)
    if not loss:
        logger.warning("MT5: can't work out %s's loss at the stop: %s", name, mt5.last_error())
        return None
    risk = acc.balance * settings.mt5_risk_percent / 100
    lots = lots_for_risk(risk, abs(loss), info.volume_step, info.volume_min, info.volume_max)
    if lots <= 0:
        logger.warning("MT5: %s even the smallest lot loses more than %.2f at the stop — skipped", name, risk)
        return None
    # The target at the plan's ratio from the price it's opened at, not from the candle's
    # close (plan.entry), a moment old: with a stop a few pips away, a pip either way made
    # 1:2 into 1:3.4 (the user's EURUSD, 3/11/26).
    ratio = abs(plan.target - plan.entry) / abs(plan.entry - plan.stop)
    sl, tp = round(plan.stop, info.digits), round(target_at(price, plan.stop, ratio), info.digits)
    side = "BUY" if buy else "SELL"
    if settings.mt5_mode != "trade":
        logger.info("MT5 PAPER: would %s %s %.2f lots @%s sl=%s tp=%s", side, name, lots, price, sl, tp)
        return 0
    res = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL, "symbol": name, "volume": lots, "type": order_type, "price": price,
        "sl": sl, "tp": tp, "deviation": DEVIATION, "magic": magic, "comment": comment[:31],
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling(info),
    })
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        logger.warning("MT5: %s %s refused: %s", side, name, (getattr(res, "retcode", None), getattr(res, "comment", "")) if res else mt5.last_error())
        return None
    logger.info("MT5: %s %s %.2f lots @%s sl=%s tp=%s — ticket %s", side, name, lots, res.price, sl, tp, res.order)
    fill_tp = round(target_at(res.price, plan.stop, ratio), info.digits) if res.price else tp
    if fill_tp != tp:  # filled off the quote: the target from the fill
        moved = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "symbol": name, "position": res.order, "sl": sl, "tp": fill_tp})
        ok = moved is not None and moved.retcode == mt5.TRADE_RETCODE_DONE
        logger.info("MT5: %s #%s target to %s from the fill -> %s", name, res.order, fill_tp, "done" if ok else getattr(moved, "comment", mt5.last_error()))
    return res.order


def target_at(price: float, stop: float, ratio: float) -> float:
    """The target ratio times the risk from price — above it for a long (stop under it)."""
    return price + (price - stop) * ratio


def move_stop(position, sl: float) -> bool:
    """Moves an open position's stop, its target kept; whether it moved. Connected, with
    lock held."""
    sl = round(sl, mt5.symbol_info(position.symbol).digits)
    if settings.mt5_mode != "trade":
        logger.info("MT5 PAPER: would move %s #%s's stop to %s", position.symbol, position.ticket, sl)
        return True
    res = mt5.order_send({
        "action": mt5.TRADE_ACTION_SLTP, "symbol": position.symbol, "position": position.ticket, "sl": sl, "tp": position.tp,
    })
    ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
    logger.info("MT5: %s #%s stop to %s -> %s", position.symbol, position.ticket, sl, "done" if ok else getattr(res, "comment", mt5.last_error()))
    return ok
