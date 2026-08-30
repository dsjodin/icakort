"""FastAPI-appen.

Hela arbetsflödet ligger här: logga in med BankID, synka kvitton och rätta
kategorier ska gå att göra i webbläsaren utan att exec:a in i containern.

Sidan är ett tunt skal; all data hämtas som JSON från endpoints som i sin
tur anropar ``icakort.stats``. Samma siffror som CLI:n visar.
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

from .. import categorize as categorize_mod
from .. import jobs, stats, store, sync as sync_mod
from ..kivra import auth as kivra_auth
from ..kivra.client import KivraClient
from ..normalize import name_key as make_name_key
from .security import BasicAuthMiddleware

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="icakort", docs_url=None, redoc_url=None)
app.add_middleware(BasicAuthMiddleware)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def get_conn():
    # En anslutning per request: sqlite-anslutningar får inte delas mellan trådar.
    conn = store.connect(same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def get_filters(
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    store_name: Optional[str] = Query(None, alias="store"),
    category: Optional[str] = Query(None),
) -> stats.Filters:
    return stats.Filters(
        date_from=date_from or None,
        date_to=date_to or None,
        store=store_name or None,
        category=category or None,
    )


# --------------------------------------------------------------------------
# Sidan och hälsokollen
# --------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


# --------------------------------------------------------------------------
# Inloggning och synk
# --------------------------------------------------------------------------


class SyncRequest(BaseModel):
    all_stores: bool = False
    max_receipts: int = Field(0, ge=0, le=10000)


def _run_sync(job: jobs.Job, token: kivra_auth.Token, request: SyncRequest) -> dict:
    """Hämta kvitton och kategorisera om. Delas av login- och synkjobbet."""
    conn = store.connect(same_thread=False)
    try:
        with KivraClient(token) as client:
            result = sync_mod.sync(
                conn,
                client,
                store_filter=None if request.all_stores else "ica",
                max_receipts=request.max_receipts or None,
                progress=lambda message: jobs.log(job, message.strip()),
            )
        counts = categorize_mod.recategorize(conn)
        jobs.log(job, str(result))
        return {
            "listed": result.listed,
            "fetched": result.fetched,
            "skipped": result.skipped,
            "failed": result.failed,
            "categorized": counts["total"],
            "uncategorized": counts.get("fallback", 0),
        }
    finally:
        conn.close()


@app.post("/api/job/login")
def api_login(request: SyncRequest) -> dict:
    """Starta BankID-signering och synka direkt efteråt.

    Ett enda steg, eftersom Kivra inte ger någon refresh-token: en färsk
    token är kortlivad och ska användas medan den lever.
    """

    def work(job: jobs.Job) -> dict:
        jobs.log(job, "Skanna QR-koden med BankID-appen.")
        token = kivra_auth.authenticate(show_qr=lambda payload: setattr(job, "qr_payload", payload))
        jobs.log(job, "Inloggad. Hämtar kvitton …")
        return _run_sync(job, token, request)

    return _start(jobs.runner, "login", work)


@app.post("/api/job/sync")
def api_sync(request: SyncRequest) -> dict:
    """Synka med den token som redan finns."""
    token = kivra_auth.load_token()
    if token is None or not token.is_valid:
        raise HTTPException(status_code=409, detail="Ingen giltig inloggning. Logga in först.")

    def work(job: jobs.Job) -> dict:
        jobs.log(job, "Hämtar kvitton …")
        return _run_sync(job, token, request)

    return _start(jobs.runner, "sync", work)


def _start(runner: jobs.JobRunner, kind: str, work) -> dict:
    try:
        return runner.start(kind, work).to_dict()
    except jobs.JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/job/reparse")
def api_reparse() -> dict:
    """Tolka om all sparad rådata, utan att kontakta Kivra.

    Vägen tillbaka när normaliseringen rättats: rådatan ligger kvar per
    kvitto, så hela historiken kan byggas om utan ny BankID-signering.
    """

    def work(job: jobs.Job) -> dict:
        jobs.log(job, "Tolkar om sparad rådata …")
        conn = store.connect(same_thread=False)
        try:
            count, unparsed = sync_mod.reparse(
                conn, progress=lambda message: jobs.log(job, message.strip())
            )
            counts = categorize_mod.recategorize(conn)
        finally:
            conn.close()
        jobs.log(job, f"{count} kvitton omtolkade, {counts['total']} rader.")
        if unparsed:
            jobs.log(job, f"VARNING: {unparsed} kvitton gav inga varurader.")
        return {"fetched": 0, "reparsed": count, "unparsed": unparsed,
                "uncategorized": counts.get("fallback", 0)}

    return _start(jobs.runner, "reparse", work)


@app.get("/api/job")
def api_job() -> dict:
    job = jobs.runner.current()
    return job.to_dict() if job else {"kind": None, "state": "idle", "log": [], "has_qr": False}


@app.get("/api/job/qr.svg")
def api_job_qr() -> Response:
    """Senaste BankID-bildrutan som SVG.

    Egen endpoint i stället för en sträng i JSON: då slipper frontend
    injicera markup som härrör från ett externt API i DOM:en.
    """
    job = jobs.runner.current()
    if job is None or not job.qr_payload:
        raise HTTPException(status_code=404, detail="Ingen QR-kod just nu.")

    import qrcode
    import qrcode.image.svg

    image = qrcode.make(job.qr_payload, image_factory=qrcode.image.svg.SvgPathImage)
    buffer = io.BytesIO()
    image.save(buffer)
    return Response(
        content=buffer.getvalue(),
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/session")
def api_session() -> dict:
    token = kivra_auth.load_token()
    return {
        "authenticated": bool(token and token.is_valid),
        "expires_at": token.expires_at if token else None,
    }


# --------------------------------------------------------------------------
# Kategorier
# --------------------------------------------------------------------------


class OverrideRequest(BaseModel):
    name_key: str = Field(min_length=1)
    category: str = Field(min_length=1)


@app.post("/api/overrides")
def api_set_override(
    request: OverrideRequest, conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    key = make_name_key(request.name_key) or request.name_key
    store.set_override(conn, key, request.category)
    counts = categorize_mod.recategorize(conn)
    return {"name_key": key, "category": request.category, "counts": counts}


@app.delete("/api/overrides/{name_key}")
def api_clear_override(name_key: str, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    store.clear_override(conn, name_key)
    return {"name_key": name_key, "counts": categorize_mod.recategorize(conn)}


@app.post("/api/categorize")
def api_categorize(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Kör om kategoriseringen, t.ex. efter en ändring i regelfilen."""
    try:
        counts = categorize_mod.recategorize(conn)
    except categorize_mod.RuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"counts": counts, "coverage": categorize_mod.coverage(conn)}


# --------------------------------------------------------------------------
# Statistik
# --------------------------------------------------------------------------


@app.get("/api/filters")
def api_filters(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    lo, hi = stats.date_bounds(conn)
    try:
        available = categorize_mod.load_ruleset().category_names
    except categorize_mod.RuleError:
        available = stats.categories(conn)
    return {
        "stores": stats.stores(conn),
        "categories": stats.categories(conn),
        "all_categories": available,
        "date_from": lo,
        "date_to": hi,
    }


@app.get("/api/overview")
def api_overview(
    filters: stats.Filters = Depends(get_filters),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    return {
        "summary": stats.summary(conn, filters),
        "monthly": stats.monthly(conn, filters),
        "by_store": stats.by_store(conn, filters),
        "coverage": categorize_mod.coverage(conn),
    }


@app.get("/api/categories")
def api_categories(
    filters: stats.Filters = Depends(get_filters),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    return {
        "by_category": stats.by_category(conn, filters),
        "by_month": stats.category_by_month(conn, filters),
    }


@app.get("/api/items")
def api_items(
    order: str = Query("spend", pattern="^(spend|count)$"),
    limit: int = Query(30, ge=1, le=200),
    filters: stats.Filters = Depends(get_filters),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    return {"items": stats.top_items(conn, filters, order=order, limit=limit)}


@app.get("/api/price")
def api_price(
    name_key: str = Query(..., alias="name_key"),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    return {"name_key": name_key, "points": stats.price_history(conn, name_key)}


@app.get("/api/quality")
def api_quality(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return {
        "coverage": categorize_mod.coverage(conn),
        "unknown": [dict(row) for row in categorize_mod.unknown_items(conn, limit=50)],
        "mismatched": [dict(row) for row in sync_mod.verify(conn)],
    }
