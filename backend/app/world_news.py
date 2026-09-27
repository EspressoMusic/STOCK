"""General world/market news, pulled from Yahoo Finance via yfinance.

Reuses the per-symbol news parsing from enrichment.py but sources from a
handful of broad market indices instead of a single stock, so the result
reads as general market news rather than one company's headlines."""
import logging

import yfinance as yf

from .enrichment import NewsItem, _news_item_from_raw

logger = logging.getLogger(__name__)

WORLD_NEWS_TICKERS = ["^GSPC", "^DJI", "^IXIC"]


def get_world_news(limit: int = 5) -> list[NewsItem]:
    seen_titles: set[str] = set()
    items: list[NewsItem] = []
    for symbol in WORLD_NEWS_TICKERS:
        try:
            raw_news = yf.Ticker(symbol).news or []
        except Exception:
            logger.warning("Failed to fetch world news for %s", symbol, exc_info=True)
            continue
        for raw in raw_news:
            parsed = _news_item_from_raw(raw)
            if not parsed or parsed.title in seen_titles:
                continue
            seen_titles.add(parsed.title)
            items.append(parsed)

    items.sort(key=lambda n: n.published_at or 0, reverse=True)
    return items[:limit]
