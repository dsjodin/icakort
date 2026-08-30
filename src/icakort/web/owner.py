"""Kontovy, dold bakom en nyckel i miljön.

Ägaren till varje kvitto samlas alltid in vid synk -- det är bara
presentationen som är gated. Skillnaden spelar roll: vore insamlingen
villkorad skulle attributionen saknas för allt som hämtats medan nyckeln var
osatt, och den luckan går inte att laga i efterhand eftersom rådatan inte
innehåller vilken Kivra-inkorg kvittot kom ur.

Är ICAKORT_OWNER_KEY osatt, eller stämmer inte nyckeln i sökvägen, svarar
rutterna med exakt samma 404 som vilken okänd sökväg som helst. Den som
provar sig fram kan alltså inte skilja "fel gissning" från "finns inte", och
lär sig aldrig att vyn existerar.

Vyn ligger kvar bakom det vanliga lösenordsskyddet -- en extra spärr
innanför, inte en väg förbi.
"""

from __future__ import annotations

import os
import secrets
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from starlette.requests import Request

from .. import stats

router = APIRouter()


def _configured_key() -> str | None:
    return os.environ.get("ICAKORT_OWNER_KEY") or None


def require_key(secret: str) -> None:
    """Släpp igenom bara på rätt nyckel. Allt annat ser ut som en död länk."""
    configured = _configured_key()
    if configured is None or not secrets.compare_digest(
        secret.encode("utf-8"), configured.encode("utf-8")
    ):
        raise HTTPException(status_code=404)


def _conn():
    from .. import store

    conn = store.connect(same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


@router.get("/api/o/{secret}/summary")
def owner_summary(
    secret: str,
    conn: sqlite3.Connection = Depends(_conn),
) -> dict:
    require_key(secret)
    filters = stats.Filters()
    household = stats.summary(conn, filters)
    return {
        "household_ore": household["total_ore"],
        "by_owner": stats.by_owner(conn, filters),
        "by_month": stats.owner_by_month(conn, filters),
        "unassigned": conn.execute(
            "SELECT COUNT(*) AS n FROM receipts WHERE owner_key IS NULL"
        ).fetchone()["n"],
    }


@router.get("/api/o/{secret}/items")
def owner_items(
    secret: str,
    owner: str,
    conn: sqlite3.Connection = Depends(_conn),
) -> dict:
    require_key(secret)
    return {
        "items": stats.top_items(conn, stats.Filters(owner=owner), limit=25),
    }


@router.get("/o/{secret}", response_class=HTMLResponse)
def owner_page(request: Request, secret: str) -> HTMLResponse:
    require_key(secret)
    from .app import templates

    return templates.TemplateResponse(request, "owner.html", {"secret": secret})
