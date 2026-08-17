"""Screens the Nasdaq via Yahoo Finance's (unofficial) screener API, in two modes:

- scan_nasdaq: today's extreme intraday movers (gainers/losers).
- scan_broken_stocks: a slower-moving list of beaten-down penny stocks — rock-bottom
  price and a deep decline over the trailing year, independent of what happened today.

Yahoo's own server-side sort is unreliable/stale, so we widen the server-side filter
and always re-sort client-side on a live field before trimming to the final list."""
import re
import logging
from dataclasses import dataclass
from typing import Optional

import yfinance as yf
from yfinance import EquityQuery

logger = logging.getLogger(__name__)

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
    page_size = 250
    all_quotes: list[dict] = []
    offset = 0
    while offset < fetch_cap:
        res = yf.screen(query, sortField=sort_field, sortAsc=sort_asc, size=page_size, offset=offset)
        quotes = (res or {}).get("quotes", [])
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
    except Exception:
        logger.exception("Yahoo Finance screener request failed")
        return ScanQueryResult(candidates=[], total_matches=0)

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
    except Exception:
        logger.exception("Yahoo Finance screener request failed")
        return ScanQueryResult(candidates=[], total_matches=0)

    candidates = [c for q in all_quotes if (c := _quote_to_candidate(q, exclude_derivatives))]

    # Rank by how little the company is worth overall (market cap), not the noisy
    # split-distorted 52-week-change field — missing market cap sorts last.
    candidates.sort(
        key=lambda c: c.market_cap if c.market_cap is not None else float("inf"),
    )

    return ScanQueryResult(candidates=candidates, total_matches=len(candidates))
