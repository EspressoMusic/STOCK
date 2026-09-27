"""Single-symbol live quote lookup — used by the demo portfolio (to price
holdings) and the chat bot (when a user asks about a specific symbol). Unlike
scanner.py this isn't a screen over the whole market, just one ticker via
yfinance's fast_info, with a couple of .info fields it doesn't cover."""
import logging
from dataclasses import dataclass
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)


@dataclass
class Quote:
    symbol: str
    name: Optional[str]
    price: Optional[float]
    change_percent: Optional[float]
    market_cap: Optional[int] = None


@dataclass
class SymbolMatch:
    symbol: str
    name: Optional[str]
    exchange: Optional[str]
    type: Optional[str]


def search_symbols(query: str, limit: int = 8) -> list[SymbolMatch]:
    query = query.strip()
    if not query:
        return []
    try:
        results = yf.Search(query, max_results=limit).quotes
    except Exception:
        logger.warning("Symbol search failed for %r", query, exc_info=True)
        return []

    matches: list[SymbolMatch] = []
    for r in results:
        symbol = r.get("symbol")
        if not symbol:
            continue
        matches.append(
            SymbolMatch(
                symbol=symbol,
                name=r.get("shortname") or r.get("longname"),
                exchange=r.get("exchDisp"),
                type=r.get("typeDisp"),
            )
        )
    return matches[:limit]


def get_quote(symbol: str) -> Optional[Quote]:
    try:
        ticker = yf.Ticker(symbol)
        fi = ticker.fast_info
        price = fi.get("lastPrice")
        prev_close = fi.get("previousClose")
        if price is None:
            return None
        change_percent = None
        if prev_close:
            change_percent = (price - prev_close) / prev_close * 100

        name = None
        market_cap = fi.get("marketCap")
        try:
            info = ticker.info or {}
            name = info.get("shortName") or info.get("longName")
            if market_cap is None:
                market_cap = info.get("marketCap")
        except Exception:
            pass

        return Quote(
            symbol=symbol,
            name=name,
            price=round(float(price), 6),
            change_percent=round(change_percent, 4) if change_percent is not None else None,
            market_cap=int(market_cap) if market_cap is not None else None,
        )
    except Exception:
        logger.warning("Quote fetch failed for %s", symbol, exc_info=True)
        return None
