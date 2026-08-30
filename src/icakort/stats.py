"""Aggregeringar över inköpen.

Rena funktioner mot databasen -- inget UI-beroende, så samma siffror kan
användas från CLI, dashboard och tester. Belopp returneras i ören.

Alla summor bygger på ``items.line_total_ore`` (radbelopp inklusive pant och
radrabatt). Eftersom varje rad har en kategori summerar kategorierna till
samma belopp som kvittototalen.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class Filters:
    date_from: str | None = None
    date_to: str | None = None
    store: str | None = None
    category: str | None = None
    owner: str | None = None

    def where(self, alias_receipt: str = "r", alias_item: str = "i") -> tuple[str, list]:
        clauses: list[str] = []
        params: list = []
        if self.date_from:
            clauses.append(f"{alias_receipt}.purchase_date >= ?")
            params.append(self.date_from)
        if self.date_to:
            clauses.append(f"{alias_receipt}.purchase_date <= ?")
            params.append(self.date_to)
        if self.store:
            clauses.append(f"{alias_receipt}.store_name = ?")
            params.append(self.store)
        if self.category:
            clauses.append(f"{alias_item}.category = ?")
            params.append(self.category)
        if self.owner:
            clauses.append(f"{alias_receipt}.owner_key = ?")
            params.append(self.owner)
        return (" AND ".join(clauses) if clauses else "1=1"), params


_JOIN = "FROM items i JOIN receipts r ON r.key = i.receipt_key"


def _rows(conn: sqlite3.Connection, sql: str, params: list) -> list[dict]:
    return [dict(row) for row in conn.execute(sql, params)]


def summary(conn: sqlite3.Connection, filters: Filters | None = None) -> dict:
    filters = filters or Filters()
    where, params = filters.where()
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM(i.line_total_ore), 0) AS total_ore,
               COUNT(DISTINCT r.key)              AS receipts,
               COUNT(*)                           AS items,
               MIN(r.purchase_date)               AS first_date,
               MAX(r.purchase_date)               AS last_date
        {_JOIN}
        WHERE {where}
        """,
        params,
    ).fetchone()
    result = dict(row)
    result["avg_receipt_ore"] = (
        round(result["total_ore"] / result["receipts"]) if result["receipts"] else 0
    )
    return result


def monthly(conn: sqlite3.Connection, filters: Filters | None = None) -> list[dict]:
    filters = filters or Filters()
    where, params = filters.where()
    return _rows(
        conn,
        f"""
        SELECT substr(r.purchase_date, 1, 7) AS month,
               SUM(i.line_total_ore)         AS total_ore,
               COUNT(DISTINCT r.key)         AS receipts
        {_JOIN}
        WHERE {where} AND r.purchase_date IS NOT NULL
        GROUP BY month
        ORDER BY month
        """,
        params,
    )


def by_category(conn: sqlite3.Connection, filters: Filters | None = None) -> list[dict]:
    filters = filters or Filters()
    where, params = filters.where()
    return _rows(
        conn,
        f"""
        SELECT COALESCE(i.category, 'Okategoriserat') AS category,
               SUM(i.line_total_ore)                  AS total_ore,
               COUNT(*)                               AS items
        {_JOIN}
        WHERE {where}
        GROUP BY category
        ORDER BY total_ore DESC
        """,
        params,
    )


def category_by_month(conn: sqlite3.Connection, filters: Filters | None = None) -> list[dict]:
    filters = filters or Filters()
    where, params = filters.where()
    return _rows(
        conn,
        f"""
        SELECT substr(r.purchase_date, 1, 7)          AS month,
               COALESCE(i.category, 'Okategoriserat') AS category,
               SUM(i.line_total_ore)                  AS total_ore
        {_JOIN}
        WHERE {where} AND r.purchase_date IS NOT NULL
        GROUP BY month, category
        ORDER BY month, total_ore DESC
        """,
        params,
    )


def by_store(conn: sqlite3.Connection, filters: Filters | None = None) -> list[dict]:
    filters = filters or Filters()
    where, params = filters.where()
    return _rows(
        conn,
        f"""
        SELECT COALESCE(r.store_name, 'Okänd butik') AS store,
               SUM(i.line_total_ore)                 AS total_ore,
               COUNT(DISTINCT r.key)                 AS receipts
        {_JOIN}
        WHERE {where}
        GROUP BY store
        ORDER BY total_ore DESC
        """,
        params,
    )


def top_items(
    conn: sqlite3.Connection,
    filters: Filters | None = None,
    order: str = "spend",
    limit: int = 30,
) -> list[dict]:
    filters = filters or Filters()
    where, params = filters.where()
    order_sql = "times DESC" if order == "count" else "total_ore DESC"
    return _rows(
        conn,
        f"""
        SELECT i.name_key,
               MIN(i.name)                            AS name,
               COALESCE(i.category, 'Okategoriserat') AS category,
               SUM(i.line_total_ore)                  AS total_ore,
               COUNT(*)                               AS times
        {_JOIN}
        WHERE {where} AND i.item_type = 'product' AND i.name_key <> ''
        GROUP BY i.name_key
        ORDER BY {order_sql}
        LIMIT ?
        """,
        params + [limit],
    )


def price_history(conn: sqlite3.Connection, name_key: str) -> list[dict]:
    """Styckpris över tid för en vara -- gör prisökningar synliga."""
    return _rows(
        conn,
        f"""
        SELECT r.purchase_date AS date,
               r.store_name    AS store,
               i.unit_price_ore,
               i.unit,
               i.quantity,
               i.line_total_ore
        {_JOIN}
        WHERE i.name_key = ? AND i.unit_price_ore IS NOT NULL
        ORDER BY r.purchase_date
        """,
        [name_key],
    )


def by_owner(conn: sqlite3.Connection, filters: Filters | None = None) -> list[dict]:
    """Utgifter per konto. Används bara av den dolda vyn."""
    filters = filters or Filters()
    where, params = filters.where()
    return _rows(
        conn,
        f"""
        SELECT COALESCE(r.owner_name, r.owner_key, 'Okänt konto') AS owner,
               r.owner_key                                        AS owner_key,
               SUM(i.line_total_ore)                              AS total_ore,
               COUNT(DISTINCT r.key)                              AS receipts
        {_JOIN}
        WHERE {where}
        GROUP BY r.owner_key
        ORDER BY total_ore DESC
        """,
        params,
    )


def owner_by_month(conn: sqlite3.Connection, filters: Filters | None = None) -> list[dict]:
    filters = filters or Filters()
    where, params = filters.where()
    return _rows(
        conn,
        f"""
        SELECT substr(r.purchase_date, 1, 7)                      AS month,
               COALESCE(r.owner_name, r.owner_key, 'Okänt konto') AS owner,
               SUM(i.line_total_ore)                              AS total_ore
        {_JOIN}
        WHERE {where} AND r.purchase_date IS NOT NULL
        GROUP BY month, r.owner_key
        ORDER BY month
        """,
        params,
    )


def stores(conn: sqlite3.Connection) -> list[str]:
    return [
        row["store_name"]
        for row in conn.execute(
            "SELECT DISTINCT store_name FROM receipts "
            "WHERE store_name IS NOT NULL ORDER BY store_name"
        )
    ]


def categories(conn: sqlite3.Connection) -> list[str]:
    return [
        row["category"]
        for row in conn.execute(
            "SELECT DISTINCT category FROM items WHERE category IS NOT NULL ORDER BY category"
        )
    ]


def date_bounds(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    row = conn.execute(
        "SELECT MIN(purchase_date) AS lo, MAX(purchase_date) AS hi FROM receipts"
    ).fetchone()
    return row["lo"], row["hi"]
