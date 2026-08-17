from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Text, BigInteger
)
from sqlalchemy.orm import relationship

from .db import Base


class Scan(Base):
    __tablename__ = "scans"

    id = Column(Integer, primary_key=True, index=True)
    direction = Column(String, index=True)  # "losers" | "gainers"
    category = Column(String, nullable=True, index=True)  # sector filter used, or None for "all"
    total_matches = Column(Integer, nullable=True)  # candidates found before display trimming
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    results = relationship("StockResult", back_populates="scan", cascade="all, delete-orphan")


class StockResult(Base):
    __tablename__ = "stock_results"

    id = Column(Integer, primary_key=True, index=True)
    scan_id = Column(Integer, ForeignKey("scans.id"), index=True)

    symbol = Column(String, index=True)
    name = Column(String, nullable=True)
    sector = Column(String, nullable=True)
    industry = Column(String, nullable=True)

    price = Column(Float, nullable=True)
    change_percent = Column(Float, nullable=True)
    volume = Column(BigInteger, nullable=True)
    market_cap = Column(BigInteger, nullable=True)
    fifty_two_week_high = Column(Float, nullable=True)
    fifty_two_week_low = Column(Float, nullable=True)
    fifty_two_week_change_percent = Column(Float, nullable=True)

    target_mean_price = Column(Float, nullable=True)
    target_high_price = Column(Float, nullable=True)
    target_low_price = Column(Float, nullable=True)
    recommendation_key = Column(String, nullable=True)
    num_analyst_opinions = Column(Integer, nullable=True)

    news_json = Column(Text, nullable=True)  # JSON-encoded list of {title, publisher, link, time}
    ai_summary = Column(Text, nullable=True)

    scan = relationship("Scan", back_populates="results")
