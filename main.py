"""
Mock payment-processor API for the Chowly training programme (source S2).
Amounts are in KOBO (1 naira = 100 kobo). Timestamps are UTC (ISO 8601, 'Z').
Lagos time (WAT) is UTC+1, so a payment just after midnight in Lagos belongs to the previous UTC day.
"""
import json, os, random
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

DATA = Path(__file__).parent / "data"
API_KEY = os.getenv("CHOWLY_API_KEY", "chowly-training-key")
FLAKY_RATE = float(os.getenv("FLAKY_RATE", "0"))   # e.g. 0.1 = 10% of calls fail with 503


def load(name):
    with open(DATA / f"{name}.json") as f:
        return json.load(f)


TX, RF, CB, ST = (load(n) for n in ("transactions", "refunds", "chargebacks", "settlements"))

app = FastAPI(title="Chowly Payment Processor (mock)", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


def guard(x_api_key: Optional[str] = Header(default=None),
          simulate: Optional[int] = Query(default=None, description="Force an error: 429, 500 or 503")):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
    if simulate in (429, 500, 503):
        raise HTTPException(status_code=simulate, detail="Simulated failure")
    if FLAKY_RATE and random.random() < FLAKY_RATE:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")


def parse_bound(v: Optional[str], end: bool):
    if v is None:
        return None
    try:
        if len(v) == 10:
            d = date.fromisoformat(v)
            return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc) if end else \
                datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Bad date '{v}'. Use YYYY-MM-DD or ISO 8601.")


def paginate(rows, page, per_page):
    total = len(rows)
    pages = max(1, -(-total // per_page))
    chunk = rows[(page - 1) * per_page: page * per_page]
    return {"status": True, "message": "ok", "data": chunk,
            "meta": {"total": total, "page": page, "per_page": per_page, "total_pages": pages}}


def listing(rows, field, frm, to, page, per_page, extra=None):
    lo, hi = parse_bound(frm, False), parse_bound(to, True)
    out = []
    for r in rows:
        v = r[field]
        t = datetime.fromisoformat(v.replace("Z", "+00:00")) if "T" in v else \
            datetime.fromisoformat(v + "T00:00:00+00:00")
        if lo and t < lo or hi and t > hi:
            continue
        if extra and not extra(r):
            continue
        out.append(r)
    return paginate(out, page, per_page)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/v1/transactions", dependencies=[Depends(guard)])
def transactions(frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                 status: Optional[str] = None, page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=100)):
    return listing(TX, "created_at", frm, to, page, per_page, (lambda r: r["status"] == status) if status else None)


@app.get("/api/v1/transactions/{reference}", dependencies=[Depends(guard)])
def transaction(reference: str):
    for r in TX:
        if r["reference"] == reference:
            return {"status": True, "message": "ok", "data": r}
    return JSONResponse(status_code=404, content={"status": False, "message": "Transaction not found"})


@app.get("/api/v1/refunds", dependencies=[Depends(guard)])
def refunds(frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
            page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=100)):
    return listing(RF, "created_at", frm, to, page, per_page)


@app.get("/api/v1/chargebacks", dependencies=[Depends(guard)])
def chargebacks(frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=100)):
    return listing(CB, "created_at", frm, to, page, per_page)


@app.get("/api/v1/settlements", dependencies=[Depends(guard)])
def settlements(frm: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=100)):
    summary = [{k: v for k, v in s.items() if not k.endswith("_references")} for s in ST]
    return listing(summary, "settlement_date", frm, to, page, per_page)


@app.get("/api/v1/settlements/{settlement_id}", dependencies=[Depends(guard)])
def settlement(settlement_id: str):
    for s in ST:
        if s["settlement_id"] == settlement_id:
            return {"status": True, "message": "ok", "data": s}
    return JSONResponse(status_code=404, content={"status": False, "message": "Settlement not found"})
