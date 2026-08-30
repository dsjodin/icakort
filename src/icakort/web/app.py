"""FastAPI-app för den lokala dashboarden.

Sidan är ett tunt skal; all data hämtas som JSON från endpoints här, som i
sin tur bara anropar ``icakort.stats``. Samma siffror som CLI:n visar.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from .. import categorize as categorize_mod
from .. import stats, store, sync as sync_mod

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="icakort", docs_url=None, redoc_url=None)
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


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/filters")
def api_filters(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    lo, hi = stats.date_bounds(conn)
    return {
        "stores": stats.stores(conn),
        "categories": stats.categories(conn),
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
