"""Kontovy, dold bakom en nyckel i miljön.

Ägaren till varje kvitto samlas alltid in vid synk -- det är bara
presentationen som är gated. Skillnaden spelar roll: vore insamlingen
villkorad skulle attributionen saknas för allt som hämtats medan nyckeln var
osatt, och den luckan går inte att laga i efterhand eftersom rådatan inte
innehåller vilken Kivra-inkorg kvittot kom ur.

Nyckeln står inte i sökvägen. En URL med hemligheten i hamnar i
webbläsarhistoriken och i adressfältets autocomplete, vilket är första
stället någon nyfiken tittar. I stället låses vyn upp med en POST som sätter
en HttpOnly-cookie.

Utan giltig cookie svarar rutterna med exakt samma 404 som vilken okänd
sökväg som helst, så den som provar sig fram inte kan lära sig att vyn finns.

Vyn ligger kvar bakom det vanliga lösenordsskyddet -- en extra spärr
innanför, inte en väg förbi.
"""

from __future__ import annotations

import os
import secrets
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from starlette.requests import Request

from .. import stats, store

router = APIRouter()

COOKIE_NAME = "icakort_o"


def _configured_key() -> str | None:
    return os.environ.get("ICAKORT_OWNER_KEY") or None


def _matches(candidate: str | None) -> bool:
    configured = _configured_key()
    if configured is None or not candidate:
        return False
    return secrets.compare_digest(
        candidate.encode("utf-8"), configured.encode("utf-8")
    )


def require_unlocked(request: Request) -> None:
    """Släpp igenom bara med giltig cookie. Allt annat ser ut som en död länk."""
    if not _matches(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=404)


def _conn():
    conn = store.connect(same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


class UnlockRequest(BaseModel):
    key: str = Field(min_length=1)


class ExcludeRequest(BaseModel):
    name_key: str | None = None
    receipt_key: str | None = None
    excluded: bool = True


@router.post("/api/unlock")
def unlock(request: UnlockRequest, response: Response) -> dict:
    """Byt nyckeln mot en cookie. Fel nyckel ser ut som en okänd sökväg."""
    if not _matches(request.key):
        raise HTTPException(status_code=404)
    response.set_cookie(
        COOKIE_NAME,
        request.key,
        httponly=True,
        samesite="strict",
        max_age=60 * 60 * 24 * 30,
        path="/",
    )
    return {"unlocked": True}


@router.get("/api/o/summary")
def owner_summary(
    request: Request, conn: sqlite3.Connection = Depends(_conn)
) -> dict:
    require_unlocked(request)
    # Den dolda vyn ser allt, även undantagna varor -- det är hela poängen
    # med att de är undantagna bara någon annanstans.
    filters = stats.Filters(include_excluded=True)
    household = stats.summary(conn, filters)
    return {
        "household_ore": household["total_ore"],
        "by_owner": stats.by_owner(conn, filters),
        "by_month": stats.owner_by_month(conn, filters),
        "unassigned": conn.execute(
            "SELECT COUNT(*) AS n FROM receipts WHERE owner_key IS NULL"
        ).fetchone()["n"],
        "excluded": [dict(row) for row in store.excluded_items(conn)],
    }


@router.get("/api/o/items")
def owner_items(
    request: Request,
    owner: str | None = None,
    search: str | None = None,
    conn: sqlite3.Connection = Depends(_conn),
) -> dict:
    require_unlocked(request)
    filters = stats.Filters(owner=owner, include_excluded=True)
    items = stats.top_items(conn, filters, limit=60)
    if search:
        needle = search.lower()
        items = [row for row in items if needle in (row["name"] or "").lower()]
    excluded = {row["name_key"] for row in store.excluded_items(conn)}
    for row in items:
        row["excluded"] = row["name_key"] in excluded
    return {"items": items}


@router.post("/api/o/exclude")
def owner_exclude(
    request: Request,
    payload: ExcludeRequest,
    conn: sqlite3.Connection = Depends(_conn),
) -> dict:
    """Undanta en vara ur standardvyerna.

    Markeringen finns bara här. En knapp för det i huvudvyn hade gjort själva
    existensen av undantagna varor uppenbar.
    """
    require_unlocked(request)
    changed = store.set_excluded(
        conn,
        name_key=payload.name_key,
        receipt_key=payload.receipt_key,
        excluded=payload.excluded,
    )
    return {"changed": changed, "excluded": payload.excluded}


@router.get("/o", response_class=HTMLResponse)
def owner_page(request: Request) -> HTMLResponse:
    require_unlocked(request)
    from .app import templates

    return templates.TemplateResponse(request, "owner.html")
