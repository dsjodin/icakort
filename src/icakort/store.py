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
    category        TEXT,
    category_source TEXT
);

CREATE INDEX IF NOT EXISTS idx_items_receipt ON items(receipt_key);
CREATE INDEX IF NOT EXISTS idx_items_name_key ON items(name_key);
CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);
CREATE INDEX IF NOT EXISTS idx_receipts_date ON receipts(purchase_date);

CREATE TABLE IF NOT EXISTS overrides (
    name_key   TEXT PRIMARY KEY,
    category   TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


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
    return conn


def known_receipt_keys(conn: sqlite3.Connection) -> set[str]:
    return {row["key"] for row in conn.execute("SELECT key FROM receipts")}


def save_receipt(conn: sqlite3.Connection, receipt: Receipt, raw_path: str | None = None) -> None:
    """Skriv kvitto + rader. Raderna byggs alltid om från grunden."""
    import json

    conn.execute(
        """
        INSERT INTO receipts (key, purchase_date, store_name, store_id, total_ore,
                              item_sum_ore, raw_path)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            purchase_date = excluded.purchase_date,
            store_name    = excluded.store_name,
            store_id      = excluded.store_id,
            total_ore     = excluded.total_ore,
            item_sum_ore  = excluded.item_sum_ore,
            raw_path      = excluded.raw_path
        """,
        (
            receipt.key,
            receipt.purchase_date,
            receipt.store_name,
            receipt.store_id,
            receipt.total_ore,
            receipt.item_sum_ore,
            raw_path,
        ),
    )
    conn.execute("DELETE FROM items WHERE receipt_key = ?", (receipt.key,))
    conn.executemany(
        """
        INSERT INTO items (receipt_key, line_no, item_type, section, name, name_key,
                           quantity, unit, unit_price_ore, amount_ore, discount_ore,
                           deposit_ore, line_total_ore, identifiers)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            )
            for item in receipt.items
        ],
    )
    conn.commit()


def set_override(conn: sqlite3.Connection, name_key: str, category: str) -> None:
    conn.execute(
        """
        INSERT INTO overrides (name_key, category) VALUES (?, ?)
        ON CONFLICT(name_key) DO UPDATE SET category = excluded.category
        """,
        (name_key, category),
    )
    conn.commit()


def clear_override(conn: sqlite3.Connection, name_key: str) -> None:
    conn.execute("DELETE FROM overrides WHERE name_key = ?", (name_key,))
    conn.commit()


def overrides(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        row["name_key"]: row["category"] for row in conn.execute("SELECT * FROM overrides")
    }


def apply_categories(conn: sqlite3.Connection, updates: Iterable[tuple[str, str, int]]) -> int:
    """updates: (kategori, källa, item-id)."""
    rows = list(updates)
    conn.executemany("UPDATE items SET category = ?, category_source = ? WHERE id = ?", rows)
    conn.commit()
    return len(rows)
