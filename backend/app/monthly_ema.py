"""The stocks bot's one strategy since 4/11/26 (the user's pick, over the weekly zone,
weekly_zone.py, code kept), long-term investing: stocks on monthly candles,
a candle touching the 50 EMA on the trade's side of it and in the trade's colour — closing
over the line and green is a long, closing under it and red is a short. Any other touch,
a red candle over the line say, is no signal.

Checked once a week after Friday's close (scheduler.py), on the candle of the month still
trading: its close and colour are the week's, and the rest of the month may change them.
A signal is all it sends (the user's ask, 4/11/26): the month and the week side by side with
the entry candle's range highlighted, no stop or target on them, and its trade isn't watched,
so nothing follows when one would be reached. The stop behind the candle, 1:3, is still worked
out, to drop a short whose target would be under 0. The same stocks as the weekly zone (US and
Tel Aviv), those with 50 months of candles; once a month per stock (sent_alerts). More signals
than a run sends: those closest to the line first, the rest may go the next week.
`python -m app.monthly_ema N` sends the last N signals of the big names, marked as examples."""
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

import yfinance as yf
from sqlalchemy.orm import Session

from . import alert_chart, telegram, trade_tracker, weekly_zone
from .candles import Candle, _to_candles
from .config import settings
from .misc_strategies import LARGE_CAPS
from .nasdaq_scalp import ema_series
from .technical_scan import _get_stock_universe, _stock_fetch_limiter
from .trade_plan import TradePlan, behind, plan
from .weekly_zone import AFTER_BARS, PICTURE_BARS, _iso
from .zone_touch_scan import send_new
from .zones import typical_candle

logger = logging.getLogger(__name__)

BOT = "stocks"
TAG = "EMA 50 חודשי"
EMA_PERIOD = 50
MONTH = 31 * 86400
HISTORY_PERIOD = "20y"  # plenty for the EMA to settle
MAX_ALERTS = weekly_zone.MAX_ALERTS


def _month(t: int) -> str:
    """A candle's month, read at midday: a stamp late the evening before stays in it."""
    return f"{datetime.fromtimestamp(t + 43200, timezone.utc):%Y-%m}"


@dataclass
class Signal:
    symbol: str
    name: Optional[str]
    plan: TradePlan
    candles: list[Candle] = field(default_factory=list, repr=False)  # up to the signal's month
    emas: list[float] = field(default_factory=list, repr=False)  # an example's run on over after
    after: list[Candle] = field(default_factory=list, repr=False)  # an example's: the months after it

    @property
    def direction(self) -> str:
        return self.plan.direction

    @property
    def time(self) -> int:
        return self.candles[-1].time

    @property
    def key(self) -> str:
        return f"ema50:{self.symbol}:{_month(self.time)}"

    @property
    def distance(self) -> float:
        """How far from the line it closed, as a share of its price."""
        return abs(self.plan.entry - self.emas[len(self.candles) - 1]) / self.plan.entry


_run_lock = threading.Lock()


def direction_at(c: Candle, ema: float) -> Optional[str]:
    """The trade when the candle touches the line, on that side of it and in that colour."""
    if not c.low <= ema <= c.high:
        return None
    if c.close > ema and c.close > c.open:
        return "up"
    if c.close < ema and c.close < c.open:
        return "down"
    return None


def signal_at(symbol: str, name: Optional[str], candles: Sequence[Candle], emas: Sequence[float], i: int) -> Optional[Signal]:
    """The signal on month i, seeing only the months up to it."""
    if i + 1 < EMA_PERIOD:
        return None
    direction = direction_at(candles[i], emas[i])
    if direction is None:
        return None
    c = candles[i]
    unit = typical_candle(candles[i + 1 - EMA_PERIOD : i + 1]) * c.close
    trade = plan(direction, c.close, behind([c], direction, emas[i], unit))
    if trade is None or trade.target <= 0:  # a short's 1:3 off a wide candle can go under 0
        return None
    return Signal(symbol, name, trade, list(candles[: i + 1]), list(emas[: i + 1]))


def find_signals(symbol: str, name: Optional[str], candles: Sequence[Candle]) -> list[Signal]:
    """Every signal on these months, oldest first."""
    emas = ema_series(candles, EMA_PERIOD)
    out = []
    for i in range(len(candles)):
        if s := signal_at(symbol, name, candles, emas, i):
            s.after = list(candles[i + 1 :])
            s.emas = list(emas[: i + 1 + AFTER_BARS])
            out.append(s)
    return out


def fetch(symbol: str, period: str = HISTORY_PERIOD, interval: str = "1mo") -> list[Candle]:
    """The symbol's candles (months unless asked otherwise), the last one still trading."""
    _stock_fetch_limiter.wait()
    try:
        df = yf.Ticker(symbol).history(interval=interval, period=period)
    except Exception:
        logger.warning("Monthly EMA scan: history fetch failed for %s", symbol)
        return []
    return _to_candles(df.dropna()) if df is not None and not df.empty else []


def _evaluate(symbol: str, name: Optional[str]) -> Optional[Signal]:
    """The signal on this month's candle so far."""
    candles = fetch(symbol)
    if len(candles) < EMA_PERIOD or time.time() - candles[-1].time > MONTH:
        return None
    return signal_at(symbol, name, candles, ema_series(candles, EMA_PERIOD), len(candles) - 1)


def scan() -> list[Signal]:
    universe = _get_stock_universe(
        weekly_zone.UNIVERSE_SIZE, min_volume=weekly_zone.UNIVERSE_MIN_VOLUME, exchanges=weekly_zone.EXCHANGES
    )
    universe += weekly_zone._tase_universe()
    if not universe:
        logger.warning("Monthly EMA scan: empty stock universe (screener unavailable?)")
    found: list[Signal] = []
    with ThreadPoolExecutor(max_workers=settings.technical_scan_workers) as pool:
        futures = [pool.submit(_evaluate, sym, name) for sym, name in universe]
        for fut in as_completed(futures):
            if (s := fut.result()) is not None:
                found.append(s)
    found.sort(key=lambda s: s.distance)
    logger.info("Monthly EMA scan: %d signals in %d stocks", len(found), len(universe))
    return found


def format_message(s: Signal, example: bool = False) -> str:
    text = telegram.alert_caption(s.symbol, None, s.direction == "up", TAG)
    if example:
        text += f"\n🕘 דוגמה מההיסטוריה · החודש {datetime.fromtimestamp(s.time + 43200, timezone.utc):%m/%y}"
    return text


def split_weeks(weeks: Sequence[Candle], s: Signal) -> tuple[list[Candle], list[Candle]]:
    """The weeks up to the last of the signal's month, and those after it."""
    i = sum(_month(w.time) <= _month(s.time) for w in weeks)
    return list(weeks[:i]), list(weeks[i:])


def picture(s: Signal) -> bytes:
    """The month with its line and the week beside it, the entry month's range highlighted
    and its arrow on both. The month alone if the weeks can't be had."""
    c = s.candles[-1]
    marks = dict(direction=s.direction, zone_colors=alert_chart.GOLD, time_format="%m/%y", min_bars=PICTURE_BARS)
    month = (
        (s.candles, f"{s.symbol} · 1M · EMA 50", c.low, c.high, None),
        dict(marks, breakout=_iso(s.time), line=s.emas, after=s.after[:AFTER_BARS]),
    )
    weeks, later = split_weeks(fetch(s.symbol, interval="1wk"), s)
    if not weeks:
        return alert_chart.render(*month[0], **month[1])
    return alert_chart.render_side_by_side(month, (
        (weeks, f"{s.symbol} · 1W", c.low, c.high, None),
        dict(marks, breakout=_iso(weeks[-1].time), after=later[:AFTER_BARS]),
    ))


def send_alert(s: Signal, example: bool = False) -> bool:
    try:
        png = picture(s)
    except Exception:
        logger.exception("Monthly EMA: chart picture failed for %s, sending text only", s.key)
        png = None
    return trade_tracker.send_alert(png, format_message(s, example), BOT, s.key, s.symbol, None, None)


def run_monthly_ema(db: Session) -> int:
    """Scans this month's candles and sends each new signal; returns how many went out."""
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
