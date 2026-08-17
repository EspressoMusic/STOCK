import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .config import settings
from .db import init_db, get_db
from .models import Scan
from .scanner import VALID_SECTORS
from .schemas import ScanOut, ScanSummaryOut, RunScanRequest
from .pipeline import run_scan
from .scheduler import start_scheduler, next_run_times

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield


app = FastAPI(title="Nasdaq Extreme Movers Bot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"service": "Nasdaq Extreme Movers Bot API", "docs": "/docs"}


@app.get("/api/sectors")
def get_sectors():
    return VALID_SECTORS


@app.get("/api/status")
def get_status():
    return {
        "openai_configured": bool(settings.openai_api_key),
        "next_scans": next_run_times(),
        "scan_times": [settings.scan_time_1, settings.scan_time_2],
        "broken_scan_time": settings.broken_scan_time,
        "timezone": settings.scan_timezone,
    }


@app.get("/api/scans", response_model=list[ScanSummaryOut])
def list_scans(
    direction: str | None = None,
    category: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    q = db.query(Scan)
    if direction:
        q = q.filter(Scan.direction == direction)
    if category:
        q = q.filter(Scan.category == category)
    scans = q.order_by(desc(Scan.created_at)).limit(limit).all()
    return [
        ScanSummaryOut(
            id=s.id, direction=s.direction, category=s.category,
            created_at=s.created_at, result_count=len(s.results),
        )
        for s in scans
    ]


@app.get("/api/scans/latest", response_model=ScanOut)
def latest_scan(
    direction: str = Query(..., pattern="^(losers|gainers|broken)$"),
    category: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(Scan).filter(Scan.direction == direction)
    q = q.filter(Scan.category == category) if category else q.filter(Scan.category.is_(None))
    scan = q.order_by(desc(Scan.created_at)).first()
    if not scan:
        raise HTTPException(status_code=404, detail="עדיין אין סריקה שמורה לסינון הזה. הרץ סריקה ידנית כדי להתחיל.")
    return ScanOut.from_orm_full(scan)


@app.get("/api/scans/{scan_id}", response_model=ScanOut)
def get_scan(scan_id: int, db: Session = Depends(get_db)):
    scan = db.get(Scan, scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    return ScanOut.from_orm_full(scan)


@app.post("/api/scans/run", response_model=ScanOut)
def trigger_scan(req: RunScanRequest, db: Session = Depends(get_db)):
    if req.direction not in ("losers", "gainers", "broken"):
        raise HTTPException(status_code=400, detail="direction must be 'losers', 'gainers', or 'broken'")
    scan = run_scan(
        db,
        direction=req.direction,
        sector=req.sector,
        max_price=req.max_price,
        min_abs_percent=req.min_abs_percent,
    )
    return ScanOut.from_orm_full(scan)
