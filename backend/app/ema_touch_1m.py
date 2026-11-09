"""The scalp bot — the user's (4/11/26): EUR/USD on 1-minute candles, signals only, it opens
no trade. When a candle that touched the 50 EMA closes green over it (long) or red under it
(short), even if it crossed the EMA to get there, a picture and a line go to its own
Telegram bot (TELEGRAM_SCALP_BOT_TOKEN), if the touch is either:

- clean: no candle touched the EMA in the last CLEAN_BARS;
- a retest: price broke through the EMA, on it for at most MAX_BREAK_BARS candles, from the
  side it had mostly been on before, stayed away at least MIN_AWAY_BARS and now comes back
  for the first time.

Candles from the MetaTrader 5 terminal, with no delay, seconds after each closes
(scheduler.py). On 7 weeks of candles (to 4/11/26) about 14 signals a day; price went 5 pips
the signal's way before 5 against 54% of the times, before costs — 52% on every first touch
of the EMA with the same close, so the clean and retest rules add little."""
import html
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from . import alert_chart, mt5_trader, telegram
from .candles import Candle
from .config import settings
from .ema_touch3_5m import server_now
from .mt5_closed import broker_offset
from .nasdaq_scalp import ema_series
from .zone_touch_scan import send_new

logger = logging.getLogger(__name__)

SYMBOL = "EURUSD"
BAR_SECONDS = 60
EMA_PERIOD = 50
CLEAN_BARS = 30
MIN_AWAY_BARS = 5
MAX_BREAK_BARS = 3
# The side it broke from: this share of the candles before the break.
PRIOR_BARS = 20
PRIOR_SHARE = 0.7
# Enough candles for the EMA to settle.
HISTORY_BARS = 400
PICTURE_BARS = 90
KINDS = {"clean": "נגיעה נקייה", "retest": "שבירה וריטסט"}


@dataclass
class TouchSignal:
    symbol: str  # the broker's
    kind: str  # KINDS
    direction: str  # "up" | "down"
    away: int  # the candles since the last that touched the EMA
    candles: list[Candle] = field(default_factory=list)  # on the real clock, closed, the last one the touch
    emas: list[float] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"ema-touch-1m:{self.candles[-1].time}"


def side(c: Candle, ema: float) -> int:
    """1 over the EMA, -1 under it, 0 touching it."""
    return 1 if c.low > ema else -1 if c.high < ema else 0


def closed_way(c: Candle, ema: float) -> Optional[str]:
    """"up" for a green candle closing over the EMA, "down" for a red one under it."""
    if c.close > c.open and c.close > ema:
        return "up"
    if c.close < c.open and c.close < ema:
        return "down"
    return None


def find_signal(candles: Sequence[Candle], emas: Sequence[float]) -> Optional[tuple[str, str, int]]:
    """(kind, direction, candles away) when the last candle, closed, is the touch that counts."""
    sides = [side(c, e) for c, e in zip(candles, emas)]
    if len(sides) < 2 or sides[-1] != 0 or sides[-2] == 0:
        return None
    direction = closed_way(candles[-1], emas[-1])
    if direction is None:
        return None
    was = sides[-2]
    away = 0
    for s in reversed(sides[:-1]):
        if s != was:
            break
        away += 1
    if away >= CLEAN_BARS:
        return "clean", direction, away
    if away < MIN_AWAY_BARS:
        return None
    end = len(sides) - 1 - away  # right after the break's candles
    on_ema = 0
    for s in reversed(sides[:end]):
        if s != 0:
            break
        on_ema += 1
    if on_ema > MAX_BREAK_BARS:
        return None
    before = sides[max(0, end - on_ema - PRIOR_BARS) : end - on_ema]
    if len(before) < PRIOR_BARS or sum(s == -was for s in before) < PRIOR_SHARE * PRIOR_BARS:
        return None
    return "retest", direction, away


def format_message(s: TouchSignal) -> str:
    up = s.direction == "up"
    return (
        f"⚡ <b>{html.escape(s.symbol)}</b> · {'לונג' if up else 'שורט'} · {KINDS[s.kind]}\n"
        f"נר {'ירוק' if up else 'אדום'} נסגר {'מעל ' if up else 'מתחת ל-'}EMA 50 · "
        f"סגירה {telegram.price(s.candles[-1].close)} · {s.away} נרות בלי נגיעה לפניו"
    )


def picture(s: TouchSignal) -> bytes:
    touch = datetime.fromtimestamp(s.candles[-1].time, timezone.utc).isoformat()
    return alert_chart.render(
        s.candles, f"{s.symbol} · 1m · EMA 50", None, None, None, direction=s.direction, touches=[touch],
        line=s.emas, time_format="%H:%M", min_bars=PICTURE_BARS, max_bars=PICTURE_BARS,
    )


def send(s: TouchSignal) -> bool:
    token, chat = telegram.bot_chat("scalp")
    try:
        png = picture(s)
    except Exception:
        logger.exception("EMA 1m: picture failed, sending text only")
        return telegram.send_message(format_message(s), chat_id=chat, bot_token=token)
    return telegram.send_photo(png, format_message(s), chat_id=chat, bot_token=token)


def look() -> Optional[TouchSignal]:
    """The signal on the candle that just closed — not on one from before a market break."""
    mt5 = mt5_trader.mt5
    with mt5_trader.lock:
        if not mt5_trader.connect() or (name := mt5_trader.broker_symbol(SYMBOL)) is None:
            return None
        mt5.symbol_select(name, True)
        now = server_now([name])
        rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_M1, 0, HISTORY_BARS)
        if now is None or rates is None:
            return None
        offset = broker_offset([name])
    closed = [r for r in rates if r["time"] + BAR_SECONDS <= now]
    if not closed or now - (closed[-1]["time"] + BAR_SECONDS) > BAR_SECONDS:
        return None
    candles = [
        Candle(int(r["time"]) - offset, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), int(r["tick_volume"]))
        for r in closed
    ]
    emas = ema_series(candles, EMA_PERIOD)
    found = find_signal(candles, emas)
    if found is None:
        return None
    kind, direction, away = found
    return TouchSignal(name, kind, direction, away, candles, emas)


def run_signals(db: Session) -> int:
    """Sends the signal of the candle that just closed, once; returns how many went out."""
    if mt5_trader.mt5 is None or not settings.telegram_scalp_bot_token:
        return 0
    signal = look()
    if signal is None:
        return 0
    sent = send_new(db, [signal], send)
    if sent:
        logger.info("EMA 1m: %s %s on %s", signal.kind, signal.direction, signal.symbol)
    return sent
