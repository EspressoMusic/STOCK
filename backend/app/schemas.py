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
    impact: Optional[str] = None


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
    avg_volume: Optional[int] = None
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
    company_blurb: Optional[str] = None
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


class TechnicalCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: Optional[str] = None
    asset_class: str
    price: float
    signal: str
    stoch_k: float
    stoch_d: float
    ema: float
    ema_period: int
    timeframe: str


class TechnicalScanOut(BaseModel):
    timeframe: str
    scanned_at: str
    universe_stock_count: int
    universe_forex_count: int
    stocks: list[TechnicalCandidateOut]
    forex: list[TechnicalCandidateOut]


class EmaBreakoutCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: Optional[str] = None
    price: float
    ema: float
    ema_period: int
    breakout_time: str
    confirmation_time: str


class EmaBreakoutScanOut(BaseModel):
    scanned_at: str
    universe_stock_count: int
    stocks: list[EmaBreakoutCandidateOut]


class EmaTouch4hCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: Optional[str] = None
    price: float
    ema: float
    ema_period: int
    candle_time: str


class EmaTouch4hScanOut(BaseModel):
    scanned_at: str
    universe_stock_count: int
    stocks: list[EmaTouch4hCandidateOut]


class EmaDoubleTouchForexCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: Optional[str] = None
    price: float
    ema: float
    ema_period: int
    first_touch_time: str
    second_touch_time: str


class EmaDoubleTouchForexScanOut(BaseModel):
    scanned_at: str
    universe_forex_count: int
    forex: list[EmaDoubleTouchForexCandidateOut]


class CandleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: Optional[int] = None


class CandlesOut(BaseModel):
    symbol: str
    timeframe: str
    candles: list[CandleOut]


class QuoteOut(BaseModel):
    symbol: str
    name: Optional[str] = None
    price: Optional[float] = None
    change_percent: Optional[float] = None
    market_cap: Optional[int] = None


class SymbolMatchOut(BaseModel):
    symbol: str
    name: Optional[str] = None
    exchange: Optional[str] = None
    type: Optional[str] = None


class SymbolSearchOut(BaseModel):
    query: str
    results: list[SymbolMatchOut] = []


class MarketCandidateOut(BaseModel):
    symbol: str
    name: Optional[str] = None
    price: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[int] = None
    market_cap: Optional[int] = None


class CryptoScanOut(BaseModel):
    scanned_at: datetime
    results: list[MarketCandidateOut] = []


class CapScanOut(BaseModel):
    size: str
    scanned_at: datetime
    total_matches: int
    results: list[MarketCandidateOut] = []


class ChartAnalysisOut(BaseModel):
    pattern: str
    confidence: int
    bias: str
    explanation: str


class ChatMessageIn(BaseModel):
    role: str
    content: str


class ChatSuggestedStock(BaseModel):
    symbol: str
    name: Optional[str] = None
    price: Optional[float] = None
    change_percent: Optional[float] = None


class ChatReplyOut(BaseModel):
    reply: str
    suggestions: list[ChatSuggestedStock] = []


class NewsDigestItemIn(BaseModel):
    title: str
    publisher: Optional[str] = None


class NewsDigestIn(BaseModel):
    symbol: str
    news: list[NewsDigestItemIn] = []


class NewsDigestOut(BaseModel):
    summary: str


class WorldNewsOut(BaseModel):
    news: list[NewsItemOut] = []
