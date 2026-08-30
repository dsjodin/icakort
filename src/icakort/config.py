"""Sökvägar och inställningar.

All persondata (token, databas, råa kvitton) hamnar under ``data/`` som är
gitignorad. Sökvägen går att flytta med miljövariabeln ICAKORT_DATA_DIR.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Kivras publika webbklient-id (samma som inbox.kivra.com använder).
# Överstyrbart via miljövariabel om Kivra byter klient.
KIVRA_CLIENT_ID = os.environ.get(
    "ICAKORT_KIVRA_CLIENT_ID", "14085255171411300228f14dceae786da5a00285fe"
)
KIVRA_REDIRECT_URI = "https://inbox.kivra.com/auth/kivra/return"
KIVRA_APP_URL = "https://app.kivra.com/"
KIVRA_API_BASE = "https://app.api.kivra.com"
KIVRA_BFF_URL = "https://bff.kivra.com/graphql"

# Hur många kvitton som hämtas per sida i listningen.
RECEIPT_PAGE_SIZE = 200

# Paus mellan detaljanrop, i sekunder. Vi hämtar vår egen data men har ingen
# anledning att hamra ett API som inte är gjort för oss.
REQUEST_DELAY_SECONDS = float(os.environ.get("ICAKORT_REQUEST_DELAY", "0.3"))


def data_dir() -> Path:
    path = Path(os.environ.get("ICAKORT_DATA_DIR", PROJECT_ROOT / "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def raw_dir() -> Path:
    path = data_dir() / "raw"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "icakort.db"


def token_path() -> Path:
    return data_dir() / "token.json"


def categories_path() -> Path:
    return Path(os.environ.get("ICAKORT_CATEGORIES", PROJECT_ROOT / "categories.yaml"))
