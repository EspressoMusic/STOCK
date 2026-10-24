import json
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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


class PatternCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: Optional[str] = None
    direction: str
    price: float
    ema: float
    ema_period: int
    zone_low: float
    zone_high: float
    touch_count: int
    last_touch_time: str
    candle_time: str


class PatternScanOut(BaseModel):
    scanned_at: str
    universe_stock_count: int
    stocks: list[PatternCandidateOut]


class TripleTouchCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: Optional[str] = None
    direction: str
    price: float
    level: float
    zone_edge: float
    touch_count: int
    at_level: bool
    distance_pct: float
    stoch_k: float
    touch_times: list[str]
    candle_time: str


class TripleTouchScanOut(BaseModel):
    market: str
    scanned_at: str
    universe_count: int
    results: list[TripleTouchCandidateOut]


class StochExtremeCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: Optional[str] = None
    signal: str
    price: float
    daily_k: float
    daily_d: float
    h4_k: float
    h4_d: float
    candle_time: str


class StochExtremeScanOut(BaseModel):
    scanned_at: str
    universe_stock_count: int
    stocks: list[StochExtremeCandidateOut]


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


class StockProfileOut(BaseModel):
    """On-demand version of a scan result's detail fields, for stocks that reach the
    detail modal from a list that doesn't carry the AI explanation (e.g. triple-touch)."""
    symbol: str
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    price: Optional[float] = None
    change_percent: Optional[float] = None
    market_cap: Optional[int] = None
    avg_volume: Optional[int] = None
    target_mean_price: Optional[float] = None
    target_high_price: Optional[float] = None
    target_low_price: Optional[float] = None
    recommendation_key: Optional[str] = None
    num_analyst_opinions: Optional[int] = None
    ai_summary: Optional[str] = None
    company_blurb: Optional[str] = None
    news: list[NewsItemOut] = []


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
    fifty_two_week_high: Optional[float] = None
    fifty_two_week_low: Optional[float] = None
    news: list[NewsItemOut] = []


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


class DailyDigestHoldingIn(BaseModel):
    symbol: str
    shares: float
    avg_price: float
    price: Optional[float] = None
    change_percent: Optional[float] = None


class DailyDigestIn(BaseModel):
    cash: float
    holdings: list[DailyDigestHoldingIn] = []


class DailyDigestOut(BaseModel):
    summary: str
    generated_at: datetime


class BillingStatusOut(BaseModel):
    plan: str  # "free" | "pro"
    ai_enabled: bool
    paddle_client_token: str = ""
    paddle_price_id: str = ""
    paddle_environment: str = "sandbox"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str  # "google" | "guest"
    email: Optional[str] = None
    name: Optional[str] = None
    picture: Optional[str] = None


class GoogleAuthIn(BaseModel):
    credential: str


class AuthResultOut(BaseModel):
    token: str
    user: UserOut


class ZoneMark(BaseModel):
    """One thing the user marked in the zone trainer. source "bot": something the detector
    drew, which they called right (verdict "ok") or wrong ("wrong"); source "user": a box
    they drew because the detector missed it (always "ok"). Stored by geometry rather than
    by detector id so it still lines up after the detector changes. Times are the unix
    seconds of the first and last candle it covers; top/bottom are prices."""
    id: str = Field(max_length=64)
    kind: Literal["zone", "breakout_retest"]
    source: Literal["bot", "user"]
    verdict: Literal["ok", "wrong"]
    from_time: int
    to_time: int
    top: float
    bottom: float
    dir: Optional[Literal["up", "down"]] = None  # breakouts the detector drew
    algo_version: Optional[int] = None  # detector version that drew it (source "bot")
    # The user's own words on why it's there, written after the trainer explained why the
    # detector missed it — read when tuning the detector.
    note: str = Field("", max_length=1000)


class ZoneTrainingIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    timeframe: str = Field(pattern="^(5m|15m|30m|1h|4h|1d|1w)$")
    marks: list[ZoneMark] = Field(default_factory=list, max_length=300)
    note: str = Field("", max_length=4000)
    params: dict = Field(default_factory=dict)  # detector settings: min_touches, algo_version
    # Frozen when the chart was first opened, as [time, open, high, low, close] rows.
    candles: list[list[float]] = Field(min_length=1, max_length=600)


class ZoneTrainingOut(ZoneTrainingIn):
    id: int
    created_at: datetime
    updated_at: datetime


class RetestSetupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: Optional[str] = None
    direction: str
    zone_bottom: float
    zone_top: float
    price: float
    breakout_time: str
    retest_times: list[str]
    bounced: bool


class RetestAlertScanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scanned_at: str
    universe_count: int
    results: list[RetestSetupOut]
    sent_count: int


class ZoneTouchSetupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: Optional[str] = None
    price: float
    zone_bottom: float
    zone_top: float
    touch_count: int
    touch_times: list[str]
    distance_pct: float


class ZoneTouchScanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scanned_at: str
    universe_count: int
    results: list[ZoneTouchSetupOut]
    sent: bool
