import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import signal_log, telegram
from app.db import Base
from app.models import SentAlert, SignalLog

RECORD = signal_log.record  # the real one — conftest replaces it in every test


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(signal_log, "PICTURE_DIR", tmp_path)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[SentAlert.__table__, SignalLog.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def caption(long, tag="", *more):
    return "\n".join([telegram.alert_caption("AAPL", None, long, tag), *more])


def test_keeps_the_side_strong_reasons_and_picture(db):
    text = caption(False, "מסחר יומי · 💪 חזק", "📅 יומי: אזור 1-2, 3 נגיעות", "🕓 4h: נר אחרון <b>אדום</b>")
    RECORD("stock-day:AAPL:down:2026-11-05:strong", "AAPL", None, text, b"png", db=db)
    row = db.query(SignalLog).one()
    assert (row.kind, row.direction, row.strong) == ("day", "down", 1)
    assert row.details == "📅 יומי: אזור 1-2, 3 נגיעות\n🕓 4h: נר אחרון אדום"
    assert row.has_picture and signal_log.picture_path(row.id).read_bytes() == b"png"


def test_without_a_picture_or_reasons(db):
    RECORD("dz1h:EURUSD=X:up:2026-11-05T19:00:00+00:00", "EURUSD=X", "EUR/USD", caption(True), None, db=db)
    row = db.query(SignalLog).one()
    assert (row.kind, row.direction, row.strong, row.details, row.has_picture) == ("forex", "up", 0, None, 0)
    assert not signal_log.picture_path(row.id).exists()


def test_other_strategies_are_not_kept(db):
    RECORD("ema-touch3-5m:NAS100:up:1", "NAS100", None, caption(True), b"png", db=db)
    assert db.query(SignalLog).count() == 0


def test_lists_only_live_alerts_of_the_kind_newest_first(db):
    for key in ("weekly:A:1", "weekly:B:2", "weekly:EXAMPLE:3", "stock-day:C:up:2026-11-05"):
        RECORD(key, key.split(":")[1], None, caption(True, "שבועי"), None, db=db)
    db.add_all([SentAlert(key=k, symbol="x") for k in ("weekly:A:1", "weekly:B:2", "stock-day:C:up:2026-11-05")])
    db.commit()
    assert [r.symbol for r in signal_log.latest(db, "weekly", 10)] == ["B", "A"]
