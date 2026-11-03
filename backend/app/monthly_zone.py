"""The Nasdaq bot's strategy since 2/11/26 (the user's ask: the stocks bot's strategy, "but
monthly instead of weekly — the same idea, on monthly candles"), in place of the NAS100
scalping (nasdaq_scalp.py, code kept).

weekly_zone.py's rules, unchanged, on Yahoo's monthly candles: a zone price turned at 3+
times on the last 26 months, a month closing the trade's way past the opposite candle's
body at it, with the Stochastic RSI %K on that month under 20 for a long, over 80 for a
short; in on its close, the stop behind the zone and the visit's wicks, 1:3.

The same stocks as the weekly zone (US and Tel Aviv), checked on the 1st of each month,
once the month before is closed (scheduler.py); every signal is sent once (sent_alerts)
and its trade watched (trade_tracker.py).
`python -m app.monthly_zone N` sends the last N signals of the big names, marked as
examples."""
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional, Sequence

import yfinance as yf
from sqlalchemy.orm import Session

from . import alert_chart, telegram, trade_tracker, weekly_zone
from .candles import Candle, _to_candles
from .config import settings
from .misc_strategies import LARGE_CAPS
from .technical_scan import _get_stock_universe, _round_sig, _stock_fetch_limiter
from .weekly_zone import AFTER_BARS, NY, PICTURE_BARS, WINDOW_BARS, _iso, signal_at, stoch
from .zone_touch_scan import send_new

logger = logging.getLogger(__name__)

BOT = "nasdaq"
TAG = "חודשי"
MONTH = 31 * 86400
HISTORY_PERIOD = "10y"
MAX_ALERTS = weekly_zone.MAX_ALERTS


class Signal(weekly_zone.Signal):
    @property
    def key(self) -> str:
        return f"monthly:{self.symbol}:{self.time}"


_run_lock = threading.Lock()


def _signal(symbol: str, name: Optional[str], candles: Sequence[Candle], k, d, i: int) -> Optional[Signal]:
    got = signal_at(candles, k, i)
    if got is None:
        return None
    direction, zone, a, trade = got
    marks = sorted({*zone.touch_idx, *([a] if a < i else [])})
    return Signal(
        symbol, name, direction, i, _round_sig(zone.bottom), _round_sig(zone.top),
        [_iso(candles[t].time) for t in marks], trade, list(candles[: i + 1]), list(k[: i + 1]), list(d[: i + 1]),
    )


def find_signals(symbol: str, name: Optional[str], candles: Sequence[Candle]) -> list[Signal]:
    """Every signal on these months, oldest first."""
    k, d = stoch(candles)
    out = []
    for i in range(WINDOW_BARS // 2, len(candles)):
        if s := _signal(symbol, name, candles, k, d, i):
            s.after = list(candles[i + 1 :])
            out.append(s)
    return out


def month_closed(c: Candle, now: float) -> bool:
    """Yahoo's month is stamped on its 1st and includes the days still trading; it's
    closed once the next month starts in New York (its last close a few hours before)."""
    day = datetime.fromtimestamp(c.time + 43200, timezone.utc).date()
    first = datetime(day.year + day.month // 12, day.month % 12 + 1, 1, tzinfo=NY)
    return now >= first.timestamp()


def fetch(symbol: str, period: str = HISTORY_PERIOD) -> list[Candle]:
    """The symbol's closed months."""
    _stock_fetch_limiter.wait()
    try:
        df = yf.Ticker(symbol).history(interval="1mo", period=period)
    except Exception:
        logger.warning("Monthly zone scan: history fetch failed for %s", symbol)
        return []
    candles = _to_candles(df.dropna()) if df is not None and not df.empty else []
    return candles if candles and month_closed(candles[-1], time.time()) else candles[:-1]


def _evaluate(symbol: str, name: Optional[str]) -> Optional[Signal]:
    """The signal on the last closed month, if it's the month just ended."""
    candles = fetch(symbol)
    if len(candles) < WINDOW_BARS // 2 or time.time() - candles[-1].time > 2 * MONTH:
        return None
    k, d = stoch(candles)
    return _signal(symbol, name, candles, k, d, len(candles) - 1)


def scan() -> list[Signal]:
    universe = _get_stock_universe(
        weekly_zone.UNIVERSE_SIZE, min_volume=weekly_zone.UNIVERSE_MIN_VOLUME, exchanges=weekly_zone.EXCHANGES
    )
    universe += weekly_zone._tase_universe()
    if not universe:
        logger.warning("Monthly zone scan: empty stock universe (screener unavailable?)")
    found: list[Signal] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate, sym, name) for sym, name in universe]
        for fut in as_completed(futures):
            if (s := fut.result()) is not None:
                found.append(s)
    found.sort(key=lambda s: -len(s.touch_times))
    logger.info("Monthly zone scan: %d signals in %d stocks", len(found), len(universe))
    return found


def format_message(s: Signal, example: bool = False) -> str:
    text = telegram.alert_caption(s.symbol, None, s.direction == "up", TAG)
    if example:
        text += f"\n🕘 דוגמה מההיסטוריה · החודש {datetime.fromtimestamp(s.time + 43200, timezone.utc):%m/%y}"
    return text


def picture(s: Signal) -> bytes:
    return alert_chart.render(
        s.candles, f"{s.symbol} · 1M", s.zone_bottom, s.zone_top, s.touch_times[0],
        direction=s.direction, breakout=_iso(s.time), touches=s.touch_times, plan=s.plan,
        stoch=(s.k, s.d), time_format="%m/%y", min_bars=PICTURE_BARS, after=s.after[:AFTER_BARS],
    )


def send_alert(s: Signal, example: bool = False) -> bool:
    try:
        png = picture(s)
    except Exception:
        logger.exception("Monthly zone: chart picture failed for %s, sending text only", s.key)
        png = None
    return trade_tracker.send_alert(png, format_message(s, example), BOT, s.key, s.symbol, None, None if example else s.plan)


def run_monthly_zone(db: Session) -> int:
    """Scans the month just closed and sends each new signal; returns how many went out."""
    with _run_lock:
        found = scan()
        return send_new(db, found, send_alert, MAX_ALERTS)


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    found = [s for symbol in LARGE_CAPS for s in find_signals(symbol, None, fetch(symbol))]
    found.sort(key=lambda s: s.time)
    print(f"{len(found)} signals")
    for s in found[-count:]:
        print(" ", s.symbol, _iso(s.time), s.direction, "sent" if send_alert(s, example=True) else "FAILED")
        time.sleep(1.1)
