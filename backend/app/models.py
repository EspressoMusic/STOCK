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
    avg_volume = Column(BigInteger, nullable=True)  # trailing 3-month average daily volume
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
    company_blurb = Column(Text, nullable=True)  # one-sentence "what this company does"

    scan = relationship("Scan", back_populates="results")


class AppLicense(Base):
    """Single-row table (id=1) holding the whole deployment's plan. There is no
    per-user billing in this app — one Paddle subscription (paid by the app
    owner) flips AI features on for everyone; free is the default and never
    touches OpenAI. See app/license.py."""
    __tablename__ = "app_license"

    id = Column(Integer, primary_key=True)
    plan = Column(String, default="free", nullable=False)  # "free" | "pro"
    paddle_subscription_id = Column(String, nullable=True)
    paddle_customer_id = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class User(Base):
    """A signed-in Google account or an anonymous guest. Plan/billing is a
    single deployment-wide switch (AppLicense above), not per-user — this
    table exists so sign-in has somewhere real to land, e.g. for
    device-independent portfolio sync in the future."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String, nullable=False)  # "google" | "guest"
    google_sub = Column(String, unique=True, nullable=True, index=True)
    email = Column(String, nullable=True, index=True)
    name = Column(String, nullable=True)
    picture = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)

    sessions = relationship("AuthSession", back_populates="user", cascade="all, delete-orphan")


class AuthSession(Base):
    """An opaque bearer token the frontend holds in localStorage after Google
    sign-in or guest login. No JWT/crypto dependency needed — the token itself
    is the lookup key (a long random secret), same trust model as a Django/
    Rails session cookie."""
    __tablename__ = "auth_sessions"

    token = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="sessions")


class ZoneTrainingChart(Base):
    """One chart the user reviewed in the zone trainer (frontend/trainer.html), kept as
    training data for the zone detector (frontend/src/lib/zones.js). payload_json holds
    a frozen snapshot of the candles, the user's marks (bot zones/breakouts they called
    right or wrong, and the ones they drew because the bot missed them), a free-text
    note and the detector settings, so the chart can be replayed against any later
    version of the detector. A symbol + timeframe can be trained more than once."""
    __tablename__ = "zone_training_charts"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)


class SentAlert(Base):
    """A Telegram alert that already went out, so the daily run never sends the same
    setup twice. key identifies the setup itself (see retest_alerts.RetestSetup.key)."""
    __tablename__ = "sent_alerts"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False, index=True)
    symbol = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
