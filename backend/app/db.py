from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def _add_missing_columns():
    """create_all only creates missing tables, not missing columns on tables that
    already exist — this backfills newly added columns on an existing sqlite file
    so upgrades don't require deleting the local dev database."""
    inspector = inspect(engine)
    if "stock_results" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("stock_results")}
    if "company_blurb" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE stock_results ADD COLUMN company_blurb TEXT"))
    if "avg_volume" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE stock_results ADD COLUMN avg_volume BIGINT"))


def init_db():
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
