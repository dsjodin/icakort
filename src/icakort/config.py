"""Sökvägar och inställningar.

All persondata (token, databas, råa kvitton) och den redigerbara regelfilen
hamnar under datakatalogen, som är gitignorad lokalt och en volym i
containern. Sökvägen styrs av ICAKORT_DATA_DIR.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent

# Förlagan följer med i wheelen och kopieras till datakatalogen första gången.
DEFAULT_CATEGORIES = PACKAGE_ROOT / "categories.default.yaml"

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


def _default_data_dir() -> Path:
    """Data bredvid koden i ett källkodsträd, annars enligt XDG.

    Installerat som wheel ligger paketet i site-packages, och dit ska vi
    varken skriva token eller databas. I containern sätts ICAKORT_DATA_DIR
    ändå, så det här är fallbacken för lokal körning.
    """
    project = PACKAGE_ROOT.parents[1]
    if (project / "pyproject.toml").exists():
        return project / "data"
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "icakort"


def data_dir() -> Path:
    path = Path(os.environ.get("ICAKORT_DATA_DIR") or _default_data_dir())
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
    """Regelfilen, i datakatalogen så den går att redigera på värden."""
    override = os.environ.get("ICAKORT_CATEGORIES")
    if override:
        return Path(override)
    return data_dir() / "categories.yaml"


def _file_version(path: Path) -> int:
    """Läs version: ur en regelfil utan att tolka hela YAML:en."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("version:"):
                return int(line.split(":", 1)[1].strip())
    except (OSError, ValueError):
        pass
    return 1        # filer utan versionsrad är den ursprungliga taxonomin


def ensure_categories_file() -> Path:
    """Lägg ut förlagan, och uppgradera en fil från en äldre taxonomi.

    Utan uppgraderingen når en ny taxonomi aldrig en befintlig installation --
    filen ligger i datakatalogen och skrevs en gång vid första körningen.
    Den gamla säkras vid sidan om, så egna tillägg går att flytta över för hand.
    """
    path = categories_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        shutil.copyfile(DEFAULT_CATEGORIES, path)
        return path

    packaged = _file_version(DEFAULT_CATEGORIES)
    if _file_version(path) < packaged:
        backup = path.with_suffix(f".v{_file_version(path)}.bak")
        shutil.copyfile(path, backup)
        shutil.copyfile(DEFAULT_CATEGORIES, path)
        print(
            f"Regelfilen uppgraderad till version {packaged}. "
            f"Den gamla sparad som {backup.name}."
        )
    return path


def web_host() -> str:
    return os.environ.get("ICAKORT_HOST", "127.0.0.1")


def web_port() -> int:
    return int(os.environ.get("ICAKORT_PORT", "8000"))
