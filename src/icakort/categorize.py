"""Regelbaserad kategorisering av varurader.

Prioritetsordning:

1. Radtyp -- pant, rabatt och avgifter är alltid vad de är
2. Manuell override i databasen (``icakort categorize set ...``)
3. Reglerna i ``categories.yaml``, första träffen vinner
4. Fallback-kategorin
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import config, store

# Radtyper som aldrig ska kategoriseras efter namn.
TYPE_CATEGORIES = {
    "deposit": "Pant",
    "discount": "Rabatt",
    "modifier": "Avgifter & justeringar",
}


class RuleError(ValueError):
    """categories.yaml är felformaterad."""


def compile_literal(literal: str) -> re.Pattern[str]:
    """Kompilera en delsträngsregel till svensk sammansättningsmatchning.

    Regeln träffar om den ligger vid en ordgräns i *någon* ände. Svenska
    sammansättningar sätter regelordet först lika ofta som sist -- både
    "kycklingfilé" och "havremjölk" ska fångas -- men en träff mitt inne i
    ett ord är nästan alltid en slump: "ros" ska inte göra "kaffe
    mellanrost" till en blomma.
    """
    escaped = re.escape(literal)
    return re.compile(rf"(?<!\w){escaped}|{escaped}(?!\w)", re.IGNORECASE)


@dataclass
class Rule:
    category: str
    patterns: tuple[re.Pattern[str], ...]

    def matches(self, name_key: str) -> bool:
        return any(pattern.search(name_key) for pattern in self.patterns)


@dataclass
class Ruleset:
    rules: tuple[Rule, ...]
    fallback: str

    @property
    def category_names(self) -> list[str]:
        names = [rule.category for rule in self.rules]
        names.extend(TYPE_CATEGORIES.values())
        names.append(self.fallback)
        seen: dict[str, None] = {}
        for name in names:
            seen.setdefault(name, None)
        return list(seen)

    def classify(self, name_key: str, item_type: str) -> tuple[str, str]:
        """Returnera (kategori, källa)."""
        type_category = TYPE_CATEGORIES.get(item_type)
        if type_category:
            return type_category, "type"
        for rule in self.rules:
            if rule.matches(name_key):
                return rule.category, "rule"
        return self.fallback, "fallback"


def load_ruleset(path: Path | None = None) -> Ruleset:
    path = path or config.ensure_categories_file()
    if not path.exists():
        raise RuleError(f"Hittar ingen regelfil: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    fallback = str(data.get("fallback") or "Okategoriserat")
    rules: list[Rule] = []
    for entry in data.get("categories") or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            raise RuleError(f"Varje kategori måste ha ett 'name': {entry!r}")
        patterns: list[re.Pattern[str]] = []
        for matcher in entry.get("match") or []:
            if isinstance(matcher, str):
                patterns.append(compile_literal(matcher.lower()))
            elif isinstance(matcher, dict) and "re" in matcher:
                patterns.append(re.compile(str(matcher["re"]), re.IGNORECASE))
            else:
                raise RuleError(
                    f"Ogiltig regel i {entry['name']}: {matcher!r} "
                    "(förväntar en sträng eller {re: ...})"
                )
        rules.append(Rule(str(entry["name"]), tuple(patterns)))
    return Ruleset(tuple(rules), fallback)


def recategorize(conn: sqlite3.Connection, ruleset: Ruleset | None = None) -> dict[str, int]:
    """Sätt om kategori på alla rader. Ger statistik över utfallet."""
    ruleset = ruleset or load_ruleset()
    manual = store.overrides(conn)

    updates: list[tuple[str, str, int]] = []
    counts = {"total": 0, "override": 0, "rule": 0, "type": 0, "fallback": 0}
    for row in conn.execute("SELECT id, name_key, item_type FROM items"):
        counts["total"] += 1
        name = row["name_key"] or ""
        item_type = row["item_type"]
        if item_type in TYPE_CATEGORIES:
            category, source = TYPE_CATEGORIES[item_type], "type"
        elif name in manual:
            category, source = manual[name], "override"
        else:
            category, source = ruleset.classify(name, item_type)
        counts[source] = counts.get(source, 0) + 1
        updates.append((category, source, row["id"]))

    store.apply_categories(conn, updates)
    return counts


def unknown_items(conn: sqlite3.Connection, limit: int = 40) -> list[sqlite3.Row]:
    """Okategoriserade varor sorterade på hur mycket pengar de utgör."""
    return list(
        conn.execute(
            """
            SELECT name_key,
                   MIN(name)            AS example_name,
                   COUNT(*)             AS times,
                   SUM(line_total_ore)  AS total_ore
            FROM items
            WHERE category_source = 'fallback'
            GROUP BY name_key
            ORDER BY total_ore DESC
            LIMIT ?
            """,
            (limit,),
        )
    )


def coverage(conn: sqlite3.Connection) -> dict[str, float | int]:
    """Hur stor andel av kronorna som faktiskt är kategoriserade."""
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(line_total_ore), 0) AS total_ore,
            COALESCE(SUM(CASE WHEN category_source = 'fallback'
                              THEN line_total_ore ELSE 0 END), 0) AS unknown_ore,
            COUNT(*) AS items,
            SUM(CASE WHEN category_source = 'fallback' THEN 1 ELSE 0 END) AS unknown_items
        FROM items
        WHERE item_type IN ('product', 'return')
        """
    ).fetchone()
    total = row["total_ore"] or 0
    unknown = row["unknown_ore"] or 0
    return {
        "total_ore": total,
        "unknown_ore": unknown,
        "items": row["items"] or 0,
        "unknown_items": row["unknown_items"] or 0,
        "covered_share": 1.0 if total == 0 else max(0.0, 1 - unknown / total),
    }
