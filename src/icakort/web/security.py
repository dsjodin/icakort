"""Lösenordsskydd för dashboarden.

Dashboarden visar hela köphistoriken och är tänkt att nås från hemnätet.
Skyddet aktiveras när ICAKORT_PASSWORD är satt, och ``icakort serve``
vägrar binda en adress utanför loopback utan det -- så det inte går att
glömma bort just när det behövs.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Hälsokollen måste svara utan lösenord, annars kan Docker inte se att
# containern lever.
OPEN_PATHS = frozenset({"/healthz"})

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def configured_password() -> str | None:
    password = os.environ.get("ICAKORT_PASSWORD", "")
    return password or None


def configured_user() -> str:
    return os.environ.get("ICAKORT_USER", "icakort")


def is_loopback(host: str) -> bool:
    return host in LOOPBACK_HOSTS


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """HTTP Basic på allt utom hälsokollen."""

    async def dispatch(self, request: Request, call_next):
        password = configured_password()
        if password is None or request.url.path in OPEN_PATHS:
            return await call_next(request)

        if not _credentials_ok(request.headers.get("Authorization"), password):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="icakort"'},
                content="Autentisering krävs.",
            )
        return await call_next(request)


def _credentials_ok(header: str | None, password: str) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, IndexError):
        return False
    user, _, given = decoded.partition(":")
    # compare_digest på båda fälten: jämförelsen ska inte läcka via tidtagning.
    # Jämför bytes, inte str -- strängvarianten kastar TypeError på icke-ASCII,
    # så ett användarnamn med å/ä/ö skulle annars ge 500 i stället för 401.
    user_ok = secrets.compare_digest(user.encode("utf-8"), configured_user().encode("utf-8"))
    password_ok = secrets.compare_digest(given.encode("utf-8"), password.encode("utf-8"))
    return user_ok and password_ok
