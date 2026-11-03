"""What a bot answers when the user writes it a symbol — "EURUSD", "EUR/USD 1h", "AAPL",
"BTC": the symbol's chart with its zones drawn (alert_chart.py) and a few lines under it
— the trend, the zone price sits on and the nearest ones above and below, and whether
there's a trade by the 4h alerts' rules (zone_touch_scan.py): price on a zone, the side
it came from, not ranging, and the daily candle agreeing.

The user's pick (27/10/26): the app's own reading, no AI. 4h unless the message names
another timeframe."""
import html
import logging
import re
from dataclasses import dataclass
from typing import Optional, Sequence

import pandas as pd

from . import alert_chart, telegram
from .candles import Candle, get_candles
from .daily_candle import confirms, fetch_daily_candles
from .scanner import CRYPTO_SYMBOLS
from .technical_scan import FOREX_PAIRS, TRIPLE_TOUCH_EXTRA_FOREX, _round_sig
from .zone_touch_scan import (
    FOREX,
    MIN_TOUCHES,
    RANGE_DISTANCE,
    STOCKS,
    WINDOW_BARS,
    _iso,
    find_zone,
    is_ranging,
    trade_direction,
)
from .zones import Zone, detect_zones

logger = logging.getLogger(__name__)

DEFAULT_TIMEFRAME = "4h"
TIMEFRAMES = {"1h": "1h", "4h": "4h", "1d": "1d", "שעה": "1h", "יומי": "1d"}
PAIR_NAMES = {sym.removesuffix("=X"): name for sym, name in FOREX_PAIRS + TRIPLE_TOUCH_EXTRA_FOREX}
CRYPTO = {sym.removesuffix("-USD") for sym in CRYPTO_SYMBOLS}
# What a trader calls them → Yahoo's symbol.
ALIASES = {"NAS100": "^NDX", "US100": "^NDX", "US500": "^GSPC", "SPX": "^GSPC", "XAUUSD": "GC=F", "GOLD": "GC=F"}
TRADINGVIEW = {"^NDX": "NDX", "^GSPC": "SPX", "GC=F": "XAUUSD"}  # where Yahoo's and TradingView's differ
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}(=X)?$")
EMA_PERIOD = 50
TREND_BARS = 10  # the EMA's slope is taken over this many candles


@dataclass
class Request:
    symbol: str  # Yahoo's
    name: str  # as the user knows it
    timeframe: str


@dataclass
class Analysis:
    text: str
    png: Optional[bytes]


def parse_request(text: str) -> Optional[Request]:
    """The symbol (and timeframe) a message asks about, or None if it isn't one."""
    words = text.strip().lstrip("/").split()
    timeframes = [TIMEFRAMES[w.lower()] for w in words if w.lower() in TIMEFRAMES]
    rest = [w.upper() for w in words if w.lower() not in TIMEFRAMES]
    if len(rest) != 1 or len(timeframes) > 1:
        return None
    timeframe = timeframes[0] if timeframes else DEFAULT_TIMEFRAME
    raw = rest[0]
    compact = raw.replace("/", "").removesuffix("=X")
    if raw in ALIASES:
        return Request(ALIASES[raw], raw, timeframe)
    if compact in PAIR_NAMES:
        return Request(f"{compact}=X", PAIR_NAMES[compact], timeframe)
    coin = raw.removesuffix("-USD").removesuffix("/USD").removesuffix("USD")
    if coin in CRYPTO:
        return Request(f"{coin}-USD", coin, timeframe)
    if re.fullmatch(r"[A-Z]{3}/?[A-Z]{3}(=X)?", raw):  # any other pair; no US ticker has 6 letters
        return Request(f"{compact}=X", f"{compact[:3]}/{compact[3:]}", timeframe)
    if _SYMBOL.match(raw):
        return Request(raw, raw, timeframe)
    return None


def tradingview_symbol(symbol: str) -> str:
    """Yahoo's "BTC-USD" is TradingView's "BTCUSD"."""
    return TRADINGVIEW.get(symbol, symbol.replace("-USD", "USD"))


def trend(candles: Sequence[Candle]) -> str:
    """Up when price is above a rising 50 EMA, down when below a falling one."""
    ema = pd.Series([c.close for c in candles]).ewm(span=EMA_PERIOD, adjust=False).mean().tolist()
    rising = ema[-1] > ema[-1 - TREND_BARS]
    last = candles[-1].close
    if last > ema[-1] and rising:
        return "עולה ↗"
    if last < ema[-1] and not rising:
        return "יורדת ↘"
    return "צדדית ↔"


def verdict(symbol: str, candles: Sequence[Candle], zones: Sequence[Zone], zone: Optional[Zone]) -> str:
    """The 4h alerts' call on this chart (zone_touch_scan._evaluate), with its reason."""
    if zone is None:
        return "⚪ אין עסקה — המחיר לא על אזור"
    direction = trade_direction(candles, zone)
    if direction is None:
        return "⚪ אין עסקה — המחיר עוד לא יצא מהאזור"
    down = direction == "down"
    side, trade = ("התנגדות", "שורט") if down else ("תמיכה", "לונג")
    market = FOREX if symbol.endswith("=X") else STOCKS
    if is_ranging(candles, zones, zone, direction, RANGE_DISTANCE[market]):
        return f"⚪ אין עסקה — המחיר על {side}, אבל יש אזור חזק קרוב מולו"
    daily = fetch_daily_candles(symbol)
    if not daily or not confirms(daily, direction):
        return f"🟡 המחיר על {side} — הנר היומי עוד לא מאשר {trade}"
    return f"{'🔴' if down else '🟢'} {trade} — המחיר על {side} והנר היומי מאשר"


def _zone_line(label: str, zone: Zone, last: float) -> str:
    where = f", {abs(zone.mid - last) / last * 100:.1f}%" if not zone.bottom <= last <= zone.top else ""
    bottom, top = telegram.price(_round_sig(zone.bottom)), telegram.price(_round_sig(zone.top))
    return f"{label}: {bottom}–{top} (×{len(zone.touch_idx)}{where})"


def analyze(request: Request) -> Optional[Analysis]:
    """None when Yahoo has no (or too little) data for the symbol."""
    candles = get_candles(request.symbol, request.timeframe)
    if len(candles) < WINDOW_BARS // 2:
        return None
    window = candles[-WINDOW_BARS:]
    last = window[-1].close
    zones = detect_zones(window, min_touches=MIN_TOUCHES)
    on = find_zone(window, zones)
    above = min((z for z in zones if z is not on and z.bottom > last), key=lambda z: z.bottom, default=None)
    below = max((z for z in zones if z is not on and z.top < last), key=lambda z: z.top, default=None)

    lines = [
        f"📊 <b>{html.escape(request.name)}</b> · {request.timeframe} · {telegram.price(_round_sig(last))}",
        f"מגמה: {trend(candles)}",
    ]
    if above:
        lines.append(_zone_line("🔺 התנגדות", above, last))
    if on:
        lines.append(_zone_line("📍 על אזור", on, last))
    if below:
        lines.append(_zone_line("🔻 תמיכה", below, last))
    if not zones:
        lines.append("אין אזורים עם 3 נגיעות בגרף")
    lines += [verdict(request.symbol, window, zones, on), telegram.link(telegram.tradingview_url(tradingview_symbol(request.symbol)), "TradingView")]

    try:
        png = alert_chart.render(
            window,
            f"{request.name} · {request.timeframe}",
            on.bottom if on else None,
            on.top if on else None,
            _iso(window[on.start_idx].time) if on else None,
            touches=[_iso(window[i].time) for i in on.touch_idx] if on else (),
            extra_zones=[(z.bottom, z.top) for z in (above, below) if z],
        )
    except Exception:
        logger.exception("Analysis chart picture failed for %s, sending text only", request.symbol)
        png = None
    return Analysis("\n".join(lines), png)


def send(request: Request, chat_id: str, bot_token: Optional[str]) -> bool:
    analysis = analyze(request)
    if analysis is None:
        return telegram.send_message(
            f"❓ לא מצאתי נתונים ל-{html.escape(request.name)} — כתוב סמל כמו EURUSD או AAPL",
            chat_id=chat_id, bot_token=bot_token,
        )
    if analysis.png is None:
        return telegram.send_message(analysis.text, chat_id=chat_id, bot_token=bot_token)
    return telegram.send_photo(analysis.png, analysis.text, chat_id=chat_id, bot_token=bot_token)
