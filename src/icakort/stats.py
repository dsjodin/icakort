"""Aggregeringar över inköpen.

Rena funktioner mot databasen -- inget UI-beroende, så samma siffror kan
användas från CLI, dashboard och tester. Belopp returneras i ören.

Alla summor bygger på ``items.line_total_ore`` (radbelopp inklusive pant och
radrabatt). Eftersom varje rad har en kategori summerar kategorierna till
samma belopp som kvittototalen.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass


@dataclass
class Filters:
    date_from: str | None = None
    date_to: str | None = None
    store: str | None = None
    category: str | None = None
    group: str | None = None
    owner: str | None = None
    # Undantagna varor är osynliga som standard. Bara den dolda vyn sätter
    # den här till True.
    include_excluded: bool = False

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
        if self.group:
            clauses.append(f"{alias_item}.category_group = ?")
            params.append(self.group)
        if self.owner:
            clauses.append(f"{alias_receipt}.owner_key = ?")
            params.append(self.owner)
        if not self.include_excluded:
            clauses.append(f"{alias_item}.excluded = 0")
            clauses.append(f"{alias_receipt}.excluded = 0")
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
        GROUP BY COALESCE(i.category, 'Okategoriserat')
        ORDER BY total_ore DESC
        """,
        params,
    )


def by_group(conn: sqlite3.Connection, filters: Filters | None = None) -> list[dict]:
    """Utgifter per huvudgrupp. Det diagrammen visar -- fyrtio kategorier
    ryms inte i en läsbar stapel."""
    filters = filters or Filters()
    where, params = filters.where()
    return _rows(
        conn,
        f"""
        SELECT COALESCE(i.category_group, 'Övrigt') AS category,
               SUM(i.line_total_ore)                AS total_ore,
               COUNT(*)                             AS items
        {_JOIN}
        WHERE {where}
        GROUP BY COALESCE(i.category_group, 'Övrigt')
        ORDER BY total_ore DESC
        """,
        params,
    )


def group_by_month(conn: sqlite3.Connection, filters: Filters | None = None) -> list[dict]:
    filters = filters or Filters()
    where, params = filters.where()
    return _rows(
        conn,
        f"""
        SELECT substr(r.purchase_date, 1, 7)        AS month,
               COALESCE(i.category_group, 'Övrigt') AS category,
               SUM(i.line_total_ore)                AS total_ore
        {_JOIN}
        WHERE {where} AND r.purchase_date IS NOT NULL
        GROUP BY substr(r.purchase_date, 1, 7), COALESCE(i.category_group, 'Övrigt')
        ORDER BY month, total_ore DESC
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
        GROUP BY substr(r.purchase_date, 1, 7), COALESCE(i.category, 'Okategoriserat')
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


def price_history(
    conn: sqlite3.Connection, name_key: str, include_excluded: bool = False
) -> list[dict]:
    """Styckpris över tid för en vara -- gör prisökningar synliga.

    Den här bygger egen WHERE och fångas inte av Filters.where(), så
    exkluderingen måste upprepas här. Annars läcker en undantagen vara ut
    just i prisvyn.
    """
    hidden = "" if include_excluded else " AND i.excluded = 0 AND r.excluded = 0"
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
        WHERE i.name_key = ? AND i.unit_price_ore IS NOT NULL{hidden}
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


def groups(conn: sqlite3.Connection) -> list[str]:
    return [
        row["category_group"]
        for row in conn.execute(
            "SELECT DISTINCT category_group FROM items "
            "WHERE category_group IS NOT NULL ORDER BY category_group"
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


# ---------------------------------------------------------------------------
# Prisutveckling
#
# Tre fällor styr utformningen här:
#
# 1. Enheten måste matcha. 24,90 kr/kg och 24,90 kr/st är inte samma pris, så
#    allt grupperas på (name_key, unit) -- aldrig på name_key ensamt.
# 2. Ett medelpris över tid mäter vad du köpt, inte vad saker kostar. Byter du
#    kaffesort ser blandningsskiftet ut som inflation. Därför matchas varor
#    mellan perioder i basket_index().
# 3. Butiksskillnader måste tidskontrolleras, annars visar jämförelsen
#    inflation i stället för butik.
# ---------------------------------------------------------------------------

MIN_OBSERVATIONS = 3        # färre än så säger ingenting om en trend
MIN_STORE_OBSERVATIONS = 2


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return 0.0
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _price_observations(
    conn: sqlite3.Connection, filters: Filters | None = None
) -> list[dict]:
    """Alla rader med ett användbart styckpris, med år och månad."""
    filters = filters or Filters()
    where, params = filters.where()
    return _rows(
        conn,
        f"""
        SELECT i.name_key,
               MIN(i.name)                    AS name,
               COALESCE(i.unit, '')           AS unit,
               substr(r.purchase_date, 1, 4)  AS year,
               substr(r.purchase_date, 1, 7)  AS month,
               r.store_name                   AS store,
               i.unit_price_ore               AS price,
               i.line_total_ore               AS spend
        {_JOIN}
        WHERE {where}
          AND i.item_type = 'product'
          AND i.unit_price_ore IS NOT NULL
          AND i.unit_price_ore > 0
          AND r.purchase_date IS NOT NULL
        GROUP BY i.id
        ORDER BY r.purchase_date
        """,
        params,
    )


def price_changes(
    conn: sqlite3.Connection,
    filters: Filters | None = None,
    min_observations: int = MIN_OBSERVATIONS,
    limit: int = 40,
) -> list[dict]:
    """Prisförändring per vara: median i första halvan mot sista halvan.

    Median och inte medelvärde: ett enda felläst eller extrapris ska inte
    kunna vända en trend.
    """
    buckets: dict[tuple[str, str], list[dict]] = {}
    for row in _price_observations(conn, filters):
        buckets.setdefault((row["name_key"], row["unit"]), []).append(row)

    results = []
    for (name_key, unit), rows in buckets.items():
        if len(rows) < min_observations:
            continue
        months = sorted({row["month"] for row in rows})
        if len(months) < 2:
            continue                       # allt köpt samma månad säger inget
        split = months[len(months) // 2]
        early = [r["price"] for r in rows if r["month"] < split]
        late = [r["price"] for r in rows if r["month"] >= split]
        if not early or not late:
            continue

        first, last = _median(early), _median(late)
        if first <= 0:
            continue
        results.append(
            {
                "name_key": name_key,
                "name": rows[0]["name"],
                "unit": unit or None,
                "first_ore": round(first),
                "last_ore": round(last),
                "change_ore": round(last - first),
                "change_share": (last - first) / first,
                "observations": len(rows),
                "first_month": months[0],
                "last_month": months[-1],
            }
        )

    results.sort(key=lambda row: abs(row["change_share"]), reverse=True)
    return results[:limit]


def basket_index(
    conn: sqlite3.Connection, filters: Filters | None = None
) -> list[dict]:
    """Matchad, kedjad prisindex per år. Startåret = 100.

    Bara varor som köpts i *båda* av två angränsande år jämförs. Det är
    poängen: en korg där en vara byts ut mot en dyrare ska inte ge utslag,
    eftersom det är en förändring i vad du köper och inte i vad saker kostar.

    Prisrelationerna vägs med utgift, så mjölken du köper varje vecka betyder
    mer för indexet än saffran en gång om året.

    Jämförelsen sker **inom samma butik**. Utan det dränks prisförändringen av
    butiksmixen: har butikerna olika prisnivå räcker det att man handlat
    oftare på den dyra ena året för att indexet ska stiga, trots att inget
    pris rört sig. Priset jämförs alltså med sig självt, i samma butik, år mot år.
    """
    per_year: dict[str, dict[tuple[str, str, str], list[dict]]] = {}
    for row in _price_observations(conn, filters):
        per_year.setdefault(row["year"], {}).setdefault(
            (row["name_key"], row["unit"], row["store"] or ""), []
        ).append(row)

    years = sorted(per_year)
    if not years:
        return []

    index = [{"year": years[0], "index": 100.0, "matched": 0}]
    level = 100.0
    for previous, current in zip(years, years[1:]):
        before, after = per_year[previous], per_year[current]
        shared = set(before) & set(after)

        ratios: list[tuple[float, float]] = []
        for key in shared:
            old = _median([r["price"] for r in before[key]])
            new = _median([r["price"] for r in after[key]])
            if old <= 0:
                continue
            # Vikten tas från den TIDIGARE perioden. Vägde vi med den senare
            # skulle en vara som stigit i pris få större vikt just därför att
            # den stigit, och driva upp indexet en andra gång.
            weight = sum(abs(r["spend"]) for r in before[key]) or 1
            ratios.append((new / old, weight))

        # Viktat geometriskt medelvärde av prisrelationerna -- samma
        # elementärindex som statistikmyndigheter använder. En viktad median
        # plockar ut en enda varas relation och blir därför skakig: med tio
        # varor kan indexet falla ett år trots att allt stigit, bara för att
        # medianvaran råkade köpas billigt. Geometriskt medelvärde använder
        # alla relationer och är symmetriskt i logg-rummet, så en fördubbling
        # och en halvering tar ut varandra.
        #
        # Orimliga relationer trimmas bort: ett pris som ändrats mer än
        # dubbelt åt något håll är oftare en enhetsförväxling eller en
        # felläst rad än en verklig prisändring.
        usable = [(ratio, weight) for ratio, weight in ratios if 0.5 <= ratio <= 2.0]
        if usable:
            weight_sum = sum(weight for _, weight in usable)
            log_sum = sum(weight * math.log(ratio) for ratio, weight in usable)
            level *= math.exp(log_sum / weight_sum)

        index.append(
            {"year": current, "index": round(level, 1), "matched": len(usable)}
        )
    return index


def store_prices(
    conn: sqlite3.Connection,
    filters: Filters | None = None,
    min_stores: int = 2,
    limit: int = 40,
) -> list[dict]:
    """Styckpris per butik för samma vara, jämfört inom samma år.

    Tidskontrollen är nödvändig: utan den skulle en vara köpt på en butik 2021
    och en annan 2025 se billigare ut på den första, fast skillnaden bara är
    inflation.
    """
    grouped: dict[tuple[str, str, str], dict[str, list[float]]] = {}
    names: dict[tuple[str, str, str], str] = {}
    for row in _price_observations(conn, filters):
        if not row["store"]:
            continue
        key = (row["name_key"], row["unit"], row["year"])
        grouped.setdefault(key, {}).setdefault(row["store"], []).append(row["price"])
        names[key] = row["name"]

    results = []
    for (name_key, unit, year), stores_seen in grouped.items():
        usable = {
            store: _median(prices)
            for store, prices in stores_seen.items()
            if len(prices) >= MIN_STORE_OBSERVATIONS
        }
        if len(usable) < min_stores:
            continue
        cheapest = min(usable, key=usable.get)
        dearest = max(usable, key=usable.get)
        if usable[cheapest] <= 0:
            continue
        results.append(
            {
                "name_key": name_key,
                "name": names[(name_key, unit, year)],
                "unit": unit or None,
                "year": year,
                "cheapest_store": cheapest,
                "cheapest_ore": round(usable[cheapest]),
                "dearest_store": dearest,
                "dearest_ore": round(usable[dearest]),
                "spread_share": (usable[dearest] - usable[cheapest]) / usable[cheapest],
                "stores": len(usable),
            }
        )

    results.sort(key=lambda row: row["spread_share"], reverse=True)
    return results[:limit]
