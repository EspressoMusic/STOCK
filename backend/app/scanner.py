"""Screens the Nasdaq via Yahoo Finance's (unofficial) screener API, in two modes:

- scan_nasdaq: today's extreme intraday movers (gainers/losers).
- scan_broken_stocks: a slower-moving list of beaten-down penny stocks — rock-bottom
  price and a deep decline over the trailing year, independent of what happened today.

Yahoo's own server-side sort is unreliable/stale, so we widen the server-side filter
and always re-sort client-side on a live field before trimming to the final list."""
import re
import logging
import math
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional

import yfinance as yf
import requests
from yfinance import EquityQuery

logger = logging.getLogger(__name__)


class MarketDataUnavailable(RuntimeError):
    """The provider failed; this is not a successful scan with no matches."""


_nasdaq_lock = threading.Lock()
_nasdaq_cache = (0.0, [])


def _number(value):
    try:
        number = float(str(value).replace(",", "").replace("$", "").replace("%", ""))
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def _nasdaq_quotes():
    """Public exchange-wide download; never substitute a limited top-movers list."""
    global _nasdaq_cache
    with _nasdaq_lock:
        if time.monotonic() - _nasdaq_cache[0] < 120:
            return _nasdaq_cache[1]
        response = requests.get(
            "https://api.nasdaq.com/api/screener/stocks",
            params={"download": "true", "exchange": "nasdaq"},
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=(5, 20),
        )
        response.raise_for_status()
        rows = (response.json().get("data") or {}).get("rows")
        if not isinstance(rows, list) or not rows:
            raise MarketDataUnavailable("Nasdaq returned an invalid or empty universe")
        quotes = [{
            "symbol": row.get("symbol"), "shortName": row.get("name"),
            "regularMarketPrice": _number(row.get("lastsale")),
            "regularMarketChangePercent": _number(row.get("pctchange")),
            "regularMarketVolume": _number(row.get("volume")),
            "marketCap": _number(row.get("marketCap")),
        } for row in rows]
        if not any(q["regularMarketPrice"] is not None for q in quotes):
            raise MarketDataUnavailable("Nasdaq returned no usable prices")
        _nasdaq_cache = (time.monotonic(), quotes)
        return quotes


def _nasdaq_fallback(query, sort_field, sort_asc, fetch_cap):
    fields = {
        "intradayprice": "regularMarketPrice", "percentchange": "regularMarketChangePercent",
        "dayvolume": "regularMarketVolume", "intradaymarketcap": "marketCap",
        "ticker": "symbol",
    }

    def predicate(node):
        op, args = node["operator"].lower(), node["operands"]
        if op == "or" and all(child.get("operator") == "EQ" and child.get("operands", [None])[0] == "exchange" for child in args):
            if {child["operands"][1] for child in args} == set(NASDAQ_EXCHANGES):
                return lambda q: True
        if op in ("and", "or"):
            children = [predicate(child) for child in args]
            return lambda q: (all if op == "and" else any)(fn(q) for fn in children)
        field, *values = args
        # The exchange download is already scoped to US Nasdaq listings.
        if field == "region" and op == "eq" and values == ["us"]:
            return lambda q: True
        if field == "exchange" and op == "is-in" and set(values) == set(NASDAQ_EXCHANGES):
            return lambda q: True
        # Sector taxonomies differ; trailing-year change is absent. Fail explicitly
        # rather than silently drop either filter or invent missing market data.
        if field not in fields or op not in ("gt", "gte", "lt", "lte", "eq", "btwn"):
            raise MarketDataUnavailable(f"Nasdaq fallback cannot preserve filter {field}")
        key = fields[field]
        def match(q):
            value = q.get(key)
            if value is None:
                return False
            if op == "gt": return value > values[0]
            if op == "gte": return value >= values[0]
            if op == "lt": return value < values[0]
            if op == "lte": return value <= values[0]
            if op == "eq": return value == values[0]
            return values[0] <= value <= values[1]
        return match

    match = predicate(query.to_dict())
    if sort_field not in fields:
        raise MarketDataUnavailable("Unsupported Nasdaq sort field")
    quotes = [q for q in _nasdaq_quotes() if match(q)]
    key = fields[sort_field]
    present = [q for q in quotes if q.get(key) is not None]
    missing = [q for q in quotes if q.get(key) is None]
    present.sort(key=lambda q: q[key], reverse=not sort_asc)
    quotes = present + missing
    return quotes[:fetch_cap]

# Yahoo's screener has no predefined crypto universe and EquityQuery's region/exchange
# fields don't cover the crypto exchange ("CCC"), so the crypto list is curated and
# fetched one ticker at a time via fast_info instead (see scan_crypto below).
CRYPTO_SYMBOLS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD", "DOGE-USD",
    "ADA-USD", "AVAX-USD", "LINK-USD", "TRX-USD", "TON-USD", "DOT-USD",
    "HBAR-USD", "SHIB-USD", "LTC-USD", "BCH-USD", "NEAR-USD", "ATOM-USD",
    "XLM-USD", "ETC-USD",
]

LARGE_CAP_MIN = 10_000_000_000
SMALL_CAP_MIN = 300_000_000
SMALL_CAP_MAX = 2_000_000_000

# All three Nasdaq listing tiers
NASDAQ_EXCHANGES = ["NMS", "NGM", "NCM"]

VALID_SECTORS = [
    "Communication Services", "Utilities", "Basic Materials", "Healthcare",
    "Energy", "Technology", "Real Estate", "Consumer Cyclical",
    "Consumer Defensive", "Financial Services", "Industrials",
]

# Nasdaq ticker suffix convention: 5-letter symbols ending in W/WS = warrant,
# R = rights, U = unit (SPAC). Combined with a name check for safety.
_DERIVATIVE_SYMBOL_RE = re.compile(r"^[A-Z]{2,4}(W|WS|R|U)$")
_DERIVATIVE_NAME_RE = re.compile(r"\b(warrant|right|unit)s?\b", re.IGNORECASE)


@dataclass
class ScanCandidate:
    symbol: str
    name: Optional[str]
    price: Optional[float]
    change_percent: Optional[float]
    volume: Optional[int]
    market_cap: Optional[int] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    fifty_two_week_change_percent: Optional[float] = None


@dataclass
class ScanQueryResult:
    candidates: list[ScanCandidate]
    total_matches: int  # how many real (non-derivative) candidates matched, before display trimming


def _is_derivative(symbol: str, name: Optional[str]) -> bool:
    if _DERIVATIVE_SYMBOL_RE.match(symbol or ""):
        return True
    if name and _DERIVATIVE_NAME_RE.search(name):
        return True
    return False


def _sector_clause(sector: Optional[str]) -> Optional[EquityQuery]:
    if not sector:
        return None
    if sector not in VALID_SECTORS:
        raise ValueError(f"Unknown sector: {sector}")
    return EquityQuery("eq", ["sector", sector])


def _fetch_all_quotes(query, sort_field: str, sort_asc: bool, fetch_cap: int) -> list[dict]:
    try:
        return _fetch_yahoo_quotes(query, sort_field, sort_asc, fetch_cap)
    except Exception:
        logger.warning("Yahoo screener unavailable; trying Nasdaq public screener", exc_info=True)
        try:
            return _nasdaq_fallback(query, sort_field, sort_asc, fetch_cap)
        except Exception as exc:
            raise MarketDataUnavailable("Market data providers unavailable for this query") from exc


def _fetch_yahoo_quotes(query, sort_field: str, sort_asc: bool, fetch_cap: int) -> list[dict]:
    page_size = 250
    all_quotes: list[dict] = []
    offset = 0
    while offset < fetch_cap:
        res = yf.screen(query, sortField=sort_field, sortAsc=sort_asc, size=page_size, offset=offset)
        if not isinstance(res, dict) or not isinstance(res.get("quotes"), list):
            raise MarketDataUnavailable("Yahoo returned an invalid screener response")
        quotes = res["quotes"]
        if not quotes:
            break
        all_quotes.extend(quotes)
        offset += len(quotes)
        total_reported = (res or {}).get("total", offset)
        if offset >= total_reported or len(quotes) < page_size:
            break
    return all_quotes


def _quote_to_candidate(q: dict, exclude_derivatives: bool) -> Optional[ScanCandidate]:
    symbol = q.get("symbol")
    name = q.get("shortName") or q.get("longName")
    if not symbol:
        return None
    if exclude_derivatives and _is_derivative(symbol, name):
        return None
    return ScanCandidate(
        symbol=symbol,
        name=name,
        price=q.get("regularMarketPrice"),
        change_percent=q.get("regularMarketChangePercent"),
        volume=q.get("regularMarketVolume"),
        market_cap=q.get("marketCap"),
        fifty_two_week_high=q.get("fiftyTwoWeekHigh"),
        fifty_two_week_low=q.get("fiftyTwoWeekLow"),
        fifty_two_week_change_percent=q.get("fiftyTwoWeekChangePercent"),
    )


def scan_nasdaq(
    direction: str,
    min_abs_percent: float = 8.0,
    min_volume: int = 100_000,
    max_price: Optional[float] = None,
    sector: Optional[str] = None,
    exclude_derivatives: bool = True,
    fetch_cap: int = 500,
) -> ScanQueryResult:
    """direction: 'losers' or 'gainers'. Returns every matching candidate (paginated,
    up to fetch_cap) — display trimming is the caller's job, not the scanner's."""
    if direction not in ("losers", "gainers"):
        raise ValueError("direction must be 'losers' or 'gainers'")

    clauses = [
        EquityQuery("eq", ["region", "us"]),
        EquityQuery("is-in", ["exchange", *NASDAQ_EXCHANGES]),
        EquityQuery("gt", ["dayvolume", min_volume]),
    ]
    if direction == "losers":
        clauses.append(EquityQuery("lt", ["percentchange", -abs(min_abs_percent)]))
    else:
        clauses.append(EquityQuery("gt", ["percentchange", abs(min_abs_percent)]))

    if max_price is not None:
        clauses.append(EquityQuery("lt", ["intradayprice", max_price]))

    sector_clause = _sector_clause(sector)
    if sector_clause:
        clauses.append(sector_clause)

    query = EquityQuery("and", clauses)

    try:
        all_quotes = _fetch_all_quotes(
            query, sort_field="percentchange", sort_asc=(direction == "losers"), fetch_cap=fetch_cap,
        )
    except Exception as exc:
        logger.exception("Yahoo Finance screener request failed")
        raise MarketDataUnavailable("Yahoo Finance screener request failed") from exc

    candidates = [c for q in all_quotes if (c := _quote_to_candidate(q, exclude_derivatives))]

    # Re-sort client-side on the live field — Yahoo's server-side sort on the
    # filter field ('percentchange') can lag regularMarketChangePercent.
    candidates.sort(
        key=lambda c: c.change_percent if c.change_percent is not None else 0,
        reverse=(direction == "gainers"),
    )

    return ScanQueryResult(candidates=candidates, total_matches=len(candidates))


def scan_broken_stocks(
    max_price: float = 2.0,
    min_drawdown_percent: float = 60.0,
    min_volume: int = 20_000,
    sector: Optional[str] = None,
    exclude_derivatives: bool = True,
    fetch_cap: int = 500,
) -> ScanQueryResult:
    """Beaten-down penny stocks: trading under max_price AND down at least
    min_drawdown_percent over the trailing year — i.e. stocks that used to be worth
    real money and have since collapsed, regardless of what they did today. This is
    a standing watchlist, not tied to the twice-daily mover scans.

    Note: fiftyTwoWeekChangePercent isn't split-adjusted, so a reverse split (common
    among penny stocks fighting Nasdaq's $1 minimum-bid delisting rule) can make the
    reported drawdown look like -99%+ even when the real decline is 'only' severe.
    The filter still correctly selects "beaten down and cheap" — the exact number is
    just noisy, which is why results are ranked by market cap, not by this field."""
    clauses = [
        EquityQuery("eq", ["region", "us"]),
        EquityQuery("is-in", ["exchange", *NASDAQ_EXCHANGES]),
        EquityQuery("lt", ["intradayprice", max_price]),
        EquityQuery("lt", ["fiftytwowkpercentchange", -abs(min_drawdown_percent)]),
        EquityQuery("gt", ["dayvolume", min_volume]),
    ]

    sector_clause = _sector_clause(sector)
    if sector_clause:
        clauses.append(sector_clause)

    query = EquityQuery("and", clauses)

    try:
        all_quotes = _fetch_all_quotes(
            query, sort_field="fiftytwowkpercentchange", sort_asc=True, fetch_cap=fetch_cap,
        )
    except Exception as exc:
        logger.exception("Yahoo Finance screener request failed")
        raise MarketDataUnavailable("Yahoo Finance screener request failed") from exc

    candidates = [c for q in all_quotes if (c := _quote_to_candidate(q, exclude_derivatives))]

    # Rank by how little the company is worth overall (market cap), not the noisy
    # split-distorted 52-week-change field — missing market cap sorts last.
    candidates.sort(
        key=lambda c: c.market_cap if c.market_cap is not None else float("inf"),
    )

    return ScanQueryResult(candidates=candidates, total_matches=len(candidates))


def _crypto_quote_to_candidate(symbol: str) -> Optional[ScanCandidate]:
    try:
        fi = yf.Ticker(symbol).fast_info
        price = fi.get("lastPrice")
        prev_close = fi.get("previousClose")
        if price is None:
            return None
        change_percent = (price - prev_close) / prev_close * 100 if prev_close else None
        return ScanCandidate(
            symbol=symbol,
            name=symbol.replace("-USD", ""),
            price=float(price),
            change_percent=change_percent,
            volume=fi.get("lastVolume"),
            market_cap=fi.get("marketCap"),
        )
    except Exception:
        logger.warning("Crypto quote fetch failed for %s", symbol)
        return None


def scan_crypto(symbols: list[str] = CRYPTO_SYMBOLS, max_workers: int = 8) -> ScanQueryResult:
    """Today's movers among a curated list of major USD crypto pairs — Yahoo's
    screener has no predefined crypto universe, so each symbol is quoted directly."""
    candidates: list[ScanCandidate] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_crypto_quote_to_candidate, s) for s in symbols]
        for future in as_completed(futures):
            candidate = future.result()
            if candidate is not None:
                candidates.append(candidate)

    candidates.sort(
        key=lambda c: abs(c.change_percent) if c.change_percent is not None else 0,
        reverse=True,
    )
    return ScanQueryResult(candidates=candidates, total_matches=len(candidates))


def scan_by_cap(
    size: str,
    min_volume: int = 100_000,
    sector: Optional[str] = None,
    exclude_derivatives: bool = True,
    fetch_cap: int = 250,
) -> ScanQueryResult:
    """size: 'large' (>$10B, ranked by market cap) or 'small' ($300M-$2B, ranked
    by today's % move) — a browsing category, not a "these broke out" signal."""
    if size not in ("large", "small"):
        raise ValueError("size must be 'large' or 'small'")

    clauses = [
        EquityQuery("eq", ["region", "us"]),
        EquityQuery("is-in", ["exchange", *NASDAQ_EXCHANGES]),
        EquityQuery("gt", ["dayvolume", min_volume]),
    ]
    if size == "large":
        clauses.append(EquityQuery("gt", ["intradaymarketcap", LARGE_CAP_MIN]))
        sort_field, sort_asc = "intradaymarketcap", False
    else:
        clauses.append(EquityQuery("btwn", ["intradaymarketcap", SMALL_CAP_MIN, SMALL_CAP_MAX]))
        sort_field, sort_asc = "percentchange", False

    sector_clause = _sector_clause(sector)
    if sector_clause:
        clauses.append(sector_clause)

    query = EquityQuery("and", clauses)

    try:
        all_quotes = _fetch_all_quotes(query, sort_field=sort_field, sort_asc=sort_asc, fetch_cap=fetch_cap)
    except Exception as exc:
        logger.exception("Yahoo Finance screener request failed")
        raise MarketDataUnavailable("Yahoo Finance screener request failed") from exc

    candidates = [c for q in all_quotes if (c := _quote_to_candidate(q, exclude_derivatives))]

    if size == "large":
        candidates.sort(key=lambda c: c.market_cap if c.market_cap is not None else 0, reverse=True)
    else:
        candidates.sort(key=lambda c: c.change_percent if c.change_percent is not None else 0, reverse=True)

    return ScanQueryResult(candidates=candidates, total_matches=len(candidates))
