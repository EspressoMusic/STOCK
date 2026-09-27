"""Orchestrates a full scan: screen -> enrich -> AI summary -> persist."""
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy.orm import Session

from .config import settings
from .scanner import scan_nasdaq, scan_broken_stocks
from .enrichment import enrich_symbols, Enrichment
from .ai_summary import generate_outlook, generate_company_blurb
from .models import Scan, StockResult

logger = logging.getLogger(__name__)


def _generate_outlooks(candidates, direction, enrichment_map, max_workers: int = 5) -> dict[str, str]:
    """AI calls are independent HTTP round-trips too — run them concurrently so a
    larger result list doesn't turn a manual rescan into a multi-minute wait."""
    outlooks: dict[str, str] = {}

    def _one(c):
        return c.symbol, generate_outlook(
            symbol=c.symbol,
            name=c.name,
            price=c.price,
            change_percent=c.change_percent,
            direction=direction,
            enrichment=enrichment_map.get(c.symbol) or Enrichment(),
            fifty_two_week_change_percent=c.fifty_two_week_change_percent,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_one, c) for c in candidates]
        for future in as_completed(futures):
            symbol, outlook = future.result()
            outlooks[symbol] = outlook
    return outlooks


def _generate_company_blurbs(candidates, enrichment_map, max_workers: int = 5) -> dict[str, str]:
    blurbs: dict[str, str] = {}

    def _one(c):
        enrichment = enrichment_map.get(c.symbol) or Enrichment()
        return c.symbol, generate_company_blurb(
            symbol=c.symbol,
            name=c.name,
            sector=enrichment.sector,
            industry=enrichment.industry,
            business_summary=enrichment.business_summary,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_one, c) for c in candidates]
        for future in as_completed(futures):
            symbol, blurb = future.result()
            blurbs[symbol] = blurb
    return blurbs


def _run_query(direction: str, sector, max_price, min_abs_percent):
    if direction == "broken":
        return scan_broken_stocks(
            max_price=max_price if max_price is not None else settings.broken_max_price,
            min_drawdown_percent=min_abs_percent if min_abs_percent is not None else settings.broken_min_drawdown_percent,
            min_volume=settings.broken_min_volume,
            sector=sector,
            exclude_derivatives=settings.scan_exclude_derivatives,
        )
    return scan_nasdaq(
        direction=direction,
        min_abs_percent=min_abs_percent if min_abs_percent is not None else settings.scan_min_abs_percent,
        min_volume=settings.scan_min_volume,
        max_price=max_price,
        sector=sector,
        exclude_derivatives=settings.scan_exclude_derivatives,
    )


def run_scan(
    db: Session,
    direction: str,
    sector: str | None = None,
    max_price: float | None = None,
    min_abs_percent: float | None = None,
) -> Scan:
    if direction not in ("losers", "gainers", "broken"):
        raise ValueError("direction must be 'losers', 'gainers', or 'broken'")

    query_result = _run_query(direction, sector, max_price, min_abs_percent)
    total_matches = query_result.total_matches
    candidates = query_result.candidates[: settings.scan_max_results]

    logger.info(
        "Scan direction=%s sector=%s found %d total, showing %d",
        direction, sector, total_matches, len(candidates),
    )

    symbols = [c.symbol for c in candidates]
    enrichment_map = enrich_symbols(symbols)
    outlook_map = _generate_outlooks(candidates, direction, enrichment_map)
    blurb_map = _generate_company_blurbs(candidates, enrichment_map)

    scan = Scan(direction=direction, category=sector, total_matches=total_matches)
    db.add(scan)
    db.flush()  # get scan.id

    for c in candidates:
        enrichment = enrichment_map.get(c.symbol) or Enrichment()
        news_payload = [
            {"title": n.title, "publisher": n.publisher, "link": n.link, "published_at": n.published_at}
            for n in enrichment.news
        ]

        result = StockResult(
            scan_id=scan.id,
            symbol=c.symbol,
            name=c.name,
            sector=enrichment.sector,
            industry=enrichment.industry,
            price=c.price,
            change_percent=c.change_percent,
            volume=c.volume,
            avg_volume=enrichment.avg_volume,
            market_cap=c.market_cap,
            fifty_two_week_high=c.fifty_two_week_high,
            fifty_two_week_low=c.fifty_two_week_low,
            fifty_two_week_change_percent=c.fifty_two_week_change_percent,
            target_mean_price=enrichment.target_mean_price,
            target_high_price=enrichment.target_high_price,
            target_low_price=enrichment.target_low_price,
            recommendation_key=enrichment.recommendation_key,
            num_analyst_opinions=enrichment.num_analyst_opinions,
            news_json=json.dumps(news_payload, ensure_ascii=False),
            ai_summary=outlook_map.get(c.symbol),
            company_blurb=blurb_map.get(c.symbol),
        )
        db.add(result)

    db.commit()
    db.refresh(scan)
    return scan


def run_full_scan_cycle(db: Session):
    """Runs both mover directions with no sector filter — the twice-daily default sweep."""
    for direction in ("losers", "gainers"):
        try:
            run_scan(db, direction=direction)
        except Exception:
            logger.exception("Scheduled scan failed for direction=%s", direction)


def run_broken_scan_cycle(db: Session):
    """Once-a-day refresh of the standing broken-stocks watchlist."""
    try:
        run_scan(db, direction="broken")
    except Exception:
        logger.exception("Scheduled broken-stocks scan failed")
