"""SQLite-lagring.

Skrivningar är idempotenta: att synka samma kvitto två gånger ska inte ge
dubbletter. Kategorier är ett härlett fält som skrivs om vid varje
kategoriseringskörning, så nya regler slår igenom retroaktivt.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from . import config
from .normalize import Receipt

SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    key           TEXT PRIMARY KEY,
    purchase_date TEXT,
    store_name    TEXT,
    store_id      TEXT,
    total_ore     INTEGER,
    item_sum_ore  INTEGER,
    raw_path      TEXT,
    owner_key     TEXT,
    owner_name    TEXT,
    excluded      INTEGER NOT NULL DEFAULT 0,
    fetched_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_key     TEXT NOT NULL REFERENCES receipts(key) ON DELETE CASCADE,
    line_no         INTEGER NOT NULL,
    item_type       TEXT NOT NULL,
    section         TEXT,
    name            TEXT,
    name_key        TEXT,
    quantity        REAL,
    unit            TEXT,
    unit_price_ore  INTEGER,
    amount_ore      INTEGER NOT NULL DEFAULT 0,
    discount_ore    INTEGER NOT NULL DEFAULT 0,
    deposit_ore     INTEGER NOT NULL DEFAULT 0,
    line_total_ore  INTEGER NOT NULL DEFAULT 0,
    identifiers     TEXT,
    excluded        INTEGER NOT NULL DEFAULT 0,
    category        TEXT,
    category_group  TEXT,
    category_source TEXT
);

CREATE TABLE IF NOT EXISTS overrides (
    name_key   TEXT PRIMARY KEY,
    category   TEXT NOT NULL,
    source     TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""

# Kolumner som tillkommit efter att databaser börjat användas skarpt.
# CREATE TABLE IF NOT EXISTS rör inte en tabell som redan finns, så de måste
# läggas till explicit.
_MIGRATIONS = (
    ("receipts", "owner_key", "TEXT"),
    ("receipts", "owner_name", "TEXT"),
    ("receipts", "excluded", "INTEGER NOT NULL DEFAULT 0"),
    ("items", "excluded", "INTEGER NOT NULL DEFAULT 0"),
    ("items", "category_group", "TEXT"),
    ("overrides", "source", "TEXT"),
)

# Indexen skapas efter migreringen: ett index på owner_key kan inte skapas
# innan ALTER TABLE lagt till kolumnen.
INDEXES = """
""" + "\n".join(['CREATE INDEX IF NOT EXISTS idx_items_receipt ON items(receipt_key);', 'CREATE INDEX IF NOT EXISTS idx_items_name_key ON items(name_key);', 'CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);', 'CREATE INDEX IF NOT EXISTS idx_receipts_date ON receipts(purchase_date);', 'CREATE INDEX IF NOT EXISTS idx_receipts_owner ON receipts(owner_key);']) + """
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Lägg till kolumner som saknas. Additivt -- ingen data rörs."""
    for table, column, ddl in _MIGRATIONS:
        have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    conn.commit()

def connect(path: Path | None = None, same_thread: bool = True) -> sqlite3.Connection:
    """Öppna databasen.

    same_thread=False behövs för webblagret: FastAPI kör synkrona beroenden i
    en trådpool, så anslutningen städas inte alltid i tråden den skapades i.
    Varje request har sin egen anslutning, så det delas aldrig.
    """
    conn = sqlite3.connect(path or config.db_path(), check_same_thread=same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.executescript(INDEXES)
    return conn

def known_receipt_keys(conn: sqlite3.Connection) -> set[str]:
    return {row["key"] for row in conn.execute("SELECT key FROM receipts")}

def save_receipt(
    conn: sqlite3.Connection,
    receipt: Receipt,
    raw_path: str | None = None,
    owner_key: str | None = None,
    owner_name: str | None = None,
) -> None:
    """Skriv kvitto + rader. Raderna byggs alltid om från grunden.

    Ägaren sätts bara när den är känd. COALESCE i upserten är inte kosmetik:
    reparse kör utan token och skickar ingen ägare, så utan den hade en
    omtolkning nollställt vem som köpt vad -- och det går inte att återskapa,
    eftersom rådatan inte innehåller vilket Kivra-konto den hämtades ur.
    """
    import json

    conn.execute(
        """
        INSERT INTO receipts (key, purchase_date, store_name, store_id, total_ore,
                              item_sum_ore, raw_path, owner_key, owner_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            purchase_date = excluded.purchase_date,
            store_name    = excluded.store_name,
            store_id      = excluded.store_id,
            total_ore     = excluded.total_ore,
            item_sum_ore  = excluded.item_sum_ore,
            raw_path      = excluded.raw_path,
            owner_key     = COALESCE(excluded.owner_key,  receipts.owner_key),
            owner_name    = COALESCE(excluded.owner_name, receipts.owner_name)
        """,
        (
            receipt.key,
            receipt.purchase_date,
            receipt.store_name,
            receipt.store_id,
            receipt.total_ore,
            receipt.item_sum_ore,
            raw_path,
            owner_key,
            owner_name,
        ),
    )
    # Raderna byggs om från grunden, så undantagna varor måste minnas över en
    # omtolkning -- annars dyker en dold present upp igen efter "Tolka om".
    excluded_rows = {
        row["name_key"]
        for row in conn.execute(
            "SELECT name_key FROM items WHERE receipt_key = ? AND excluded = 1",
            (receipt.key,),
        )
    }
    conn.execute("DELETE FROM items WHERE receipt_key = ?", (receipt.key,))
    conn.executemany(
        """
        INSERT INTO items (receipt_key, line_no, item_type, section, name, name_key,
                           quantity, unit, unit_price_ore, amount_ore, discount_ore,
                           deposit_ore, line_total_ore, identifiers, excluded)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                receipt.key,
                item.line_no,
                item.item_type,
                item.section,
                item.name,
                item.name_key,
                item.quantity,
                item.unit,
                item.unit_price_ore,
                item.amount_ore,
                item.discount_ore,
                item.deposit_ore,
                item.line_total_ore,
                json.dumps(item.identifiers, ensure_ascii=False),
                1 if item.name_key in excluded_rows else 0,
            )
            for item in receipt.items
        ],
    )
    conn.commit()

def assign_owner(conn: sqlite3.Connection, owner_key: str, owner_name: str) -> int:
    """Tillskriv kvitton som saknar ägare. Rör aldrig en befintlig attribution."""
    cursor = conn.execute(
        "UPDATE receipts SET owner_key = ?, owner_name = ? WHERE owner_key IS NULL",
        (owner_key, owner_name),
    )
    conn.commit()
    return cursor.rowcount

def reset_categorisation(conn: sqlite3.Connection) -> dict[str, int]:
    """Nollställ kategoriseringen inför en ny taxonomi.

    Bara härledda fält och manuella val rörs. Kvitton, varurader, belopp,
    ägare och undantag är orörda -- kategori har alltid varit ett härlett
    fält, och det är det som gör en omstart ofarlig.
    """
    overrides_removed = conn.execute("DELETE FROM overrides").rowcount
    items_cleared = conn.execute(
        "UPDATE items SET category = NULL, category_group = NULL, category_source = NULL"
    ).rowcount
    conn.commit()
    return {"overrides": overrides_removed, "items": items_cleared}


def set_override(
    conn: sqlite3.Connection, name_key: str, category: str, source: str = "manual"
) -> None:
    conn.execute(
        """
        INSERT INTO overrides (name_key, category, source) VALUES (?, ?, ?)
        ON CONFLICT(name_key) DO UPDATE SET
            category = excluded.category,
            source   = excluded.source
        """,
        (name_key, category, source),
    )
    conn.commit()

def set_overrides_bulk(
    conn: sqlite3.Connection,
    name_keys: list[str],
    category: str,
    source: str = "manual",
) -> int:
    """Sätt samma kategori på många varor. En commit, inte en per vara."""
    rows = [(key, category, source) for key in name_keys if key]
    conn.executemany(
        """
        INSERT INTO overrides (name_key, category, source) VALUES (?, ?, ?)
        ON CONFLICT(name_key) DO UPDATE SET
            category = excluded.category,
            source   = excluded.source
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def set_overrides_from_model(
    conn: sqlite3.Connection, assignments: dict[str, str]
) -> int:
    """Skriv modellens förslag. Märks som 'llm' så de går att granska samlat."""
    rows = [(key, category, "llm") for key, category in assignments.items() if key and category]
    conn.executemany(
        """
        INSERT INTO overrides (name_key, category, source) VALUES (?, ?, ?)
        ON CONFLICT(name_key) DO UPDATE SET
            category = excluded.category,
            source   = excluded.source
        """,
        rows,
    )
    conn.commit()
    return len(rows)

def set_excluded(
    conn: sqlite3.Connection, name_key: str | None = None,
    receipt_key: str | None = None, excluded: bool = True,
) -> int:
    """Undanta en vara eller ett helt kvitto ur standardvyerna."""
    flag = 1 if excluded else 0
    if name_key:
        cursor = conn.execute(
            "UPDATE items SET excluded = ? WHERE name_key = ?", (flag, name_key)
        )
    elif receipt_key:
        cursor = conn.execute(
            "UPDATE receipts SET excluded = ? WHERE key = ?", (flag, receipt_key)
        )
    else:
        return 0
    conn.commit()
    return cursor.rowcount

def excluded_items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT name_key, MIN(name) AS name, COUNT(*) AS times,
                   SUM(line_total_ore) AS total_ore
            FROM items WHERE excluded = 1
            GROUP BY name_key ORDER BY total_ore DESC
            """
        )
    )

def clear_override(conn: sqlite3.Connection, name_key: str) -> None:
    conn.execute("DELETE FROM overrides WHERE name_key = ?", (name_key,))
    conn.commit()

def overrides(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """name_key -> (kategori, källa).

    Källan följer med: utan den går det inte att se om en kategori kom från
    Claude eller från en egen rättning, och då går ett fel inte att spåra
    till sin orsak.
    """
    return {
        row["name_key"]: (row["category"], row["source"] or "manual")
        for row in conn.execute("SELECT name_key, category, source FROM overrides")
    }


def clear_model_overrides(conn: sqlite3.Connection) -> int:
    """Släpp Claudes svar så rättade regler får gälla igen.

    En override slår alltid regeln, så en gammal modellgissning fortsätter
    annars överskugga en regel som just förbättrats. Egna rättningar rörs inte.
    """
    removed = conn.execute("DELETE FROM overrides WHERE source = 'llm'").rowcount
    conn.commit()
    return removed

def apply_categories(
    conn: sqlite3.Connection, updates: Iterable[tuple[str, str, str, int]]
) -> int:
    """updates: (kategori, grupp, källa, item-id)."""
    rows = list(updates)
    conn.executemany(
        "UPDATE items SET category = ?, category_group = ?, category_source = ? WHERE id = ?",
        rows,
    )
    conn.commit()
    return len(rows)
