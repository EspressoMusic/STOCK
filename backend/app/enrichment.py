"""Per-symbol enrichment: sector/industry, analyst targets, and recent news
headlines, pulled from Yahoo Finance via yfinance."""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    title: str
    publisher: Optional[str]
    link: Optional[str]
    published_at: Optional[int]  # unix timestamp


@dataclass
class Enrichment:
    sector: Optional[str] = None
    industry: Optional[str] = None
    target_mean_price: Optional[float] = None
    target_high_price: Optional[float] = None
    target_low_price: Optional[float] = None
    recommendation_key: Optional[str] = None
    num_analyst_opinions: Optional[int] = None
    news: list[NewsItem] = field(default_factory=list)


def _parse_pub_date(value) -> Optional[int]:
    """Yahoo returns pubDate as an ISO-8601 string ('2026-07-27T18:17:58Z'), not a
    unix timestamp — normalize it here so every downstream consumer (JSON storage,
    the API schema, the frontend's relative-time formatter) can rely on a plain int."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


def enrich_symbol(symbol: str, news_limit: int = 4) -> Enrichment:
    result = Enrichment()
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        result.sector = info.get("sector")
        result.industry = info.get("industry")
        result.target_mean_price = info.get("targetMeanPrice")
        result.target_high_price = info.get("targetHighPrice")
        result.target_low_price = info.get("targetLowPrice")
        rec = info.get("recommendationKey")
        result.recommendation_key = rec if rec and rec != "none" else None
        result.num_analyst_opinions = info.get("numberOfAnalystOpinions")
    except Exception:
        logger.warning("Failed to fetch info for %s", symbol, exc_info=True)

    try:
        raw_news = ticker.news or []
        for item in raw_news[:news_limit]:
            content = item.get("content", item)
            title = content.get("title")
            if not title:
                continue
            provider = content.get("provider")
            publisher = provider.get("displayName") if isinstance(provider, dict) else content.get("publisher")
            link = None
            link_obj = content.get("canonicalUrl") or content.get("clickThroughUrl")
            if isinstance(link_obj, dict):
                link = link_obj.get("url")
            elif isinstance(link_obj, str):
                link = link_obj
            result.news.append(NewsItem(
                title=title,
                publisher=publisher,
                link=link,
                published_at=_parse_pub_date(content.get("pubDate")),
            ))
    except Exception:
        logger.warning("Failed to fetch news for %s", symbol, exc_info=True)

    return result


def enrich_symbols(symbols: list[str], news_limit: int = 4, max_workers: int = 8) -> dict[str, Enrichment]:
    """Fetches symbols concurrently — these are independent, I/O-bound HTTP calls to
    Yahoo Finance, so a small thread pool cuts wall-clock time roughly max_workers-fold
    without hammering the endpoint any harder per-request than a sequential loop would."""
    out: dict[str, Enrichment] = {}
    if not symbols:
        return out
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(enrich_symbol, symbol, news_limit): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                out[symbol] = future.result()
            except Exception:
                logger.warning("Enrichment failed for %s", symbol, exc_info=True)
                out[symbol] = Enrichment()
    return out
