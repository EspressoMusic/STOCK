import json
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from .models import Scan, StockResult


class NewsItemOut(BaseModel):
    title: str
    publisher: Optional[str] = None
    link: Optional[str] = None
    published_at: Optional[int] = None


class StockResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    price: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[int] = None
    market_cap: Optional[int] = None
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    fifty_two_week_change_percent: Optional[float] = None
    target_mean_price: Optional[float] = None
    target_high_price: Optional[float] = None
    target_low_price: Optional[float] = None
    recommendation_key: Optional[str] = None
    num_analyst_opinions: Optional[int] = None
    ai_summary: Optional[str] = None
    news: list[NewsItemOut] = []

    @staticmethod
    def from_orm_full(row: StockResult) -> "StockResultOut":
        news = []
        if row.news_json:
            try:
                raw_items = json.loads(row.news_json)
            except Exception:
                raw_items = []
            for n in raw_items:
                try:
                    news.append(NewsItemOut(**n))
                except Exception:
                    continue  # one malformed item shouldn't drop the rest
        data = StockResultOut.model_validate(row)
        data.news = news
        return data


class ScanOut(BaseModel):
    id: int
    direction: str
    category: Optional[str] = None
    total_matches: Optional[int] = None
    created_at: datetime
    results: list[StockResultOut] = []

    @staticmethod
    def from_orm_full(scan: Scan) -> "ScanOut":
        return ScanOut(
            id=scan.id,
            direction=scan.direction,
            category=scan.category,
            total_matches=scan.total_matches,
            created_at=scan.created_at,
            results=[StockResultOut.from_orm_full(r) for r in scan.results],
        )


class ScanSummaryOut(BaseModel):
    id: int
    direction: str
    category: Optional[str] = None
    created_at: datetime
    result_count: int


class RunScanRequest(BaseModel):
    direction: str  # "losers" | "gainers"
    sector: Optional[str] = None
    max_price: Optional[float] = None
    min_abs_percent: Optional[float] = None
