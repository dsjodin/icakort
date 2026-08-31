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

# Pant, rabatt och avgifter är inte varor och hör inte hemma bland dem.
# Egen grupp också för att rabatten ska förbli synlig: nettas den mot
# varuutgifter försvinner den ur diagrammet.
TYPE_GROUP = "Pant & rabatter"

# Okategoriserat får synas för sig. Göms det bland blommor och djurmat
# ser statistiken mer komplett ut än den är.
FALLBACK_GROUP = "Okategoriserat"


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


# Hur väl en regel träffar. Svenska sammansättningar sätter huvudordet sist,
# så "korvbröd" är ett bröd och inte en korv -- en träff i ordets slut väger
# därför tyngre än en i början.
SCORE_WHOLE_WORD = 3
SCORE_HEAD = 2        # ordets slut: huvudordet i en sammansättning
SCORE_MODIFIER = 1    # ordets början: förledet


def _score_span(name_key: str, start: int, end: int) -> int:
    """Rangordna en träff efter var i ordet den ligger."""
    # Ordet träffen ligger i, avgränsat av mellanslag.
    word_start = name_key.rfind(" ", 0, start) + 1
    word_end = name_key.find(" ", end)
    if word_end == -1:
        word_end = len(name_key)

    if start == word_start and end == word_end:
        return SCORE_WHOLE_WORD
    if end == word_end:
        return SCORE_HEAD
    if start == word_start:
        return SCORE_MODIFIER
    return 0


@dataclass
class Rule:
    category: str
    group: str
    patterns: tuple[re.Pattern[str], ...]

    def match_score(self, name_key: str) -> int:
        """Bästa träffpoängen för regeln, eller 0 om den inte träffar."""
        best = 0
        for pattern in self.patterns:
            match = pattern.search(name_key)
            if match:
                best = max(best, _score_span(name_key, match.start(), match.end()))
        return best

    def matches(self, name_key: str) -> bool:
        return self.match_score(name_key) > 0


@dataclass
class Ruleset:
    rules: tuple[Rule, ...]
    fallback: str
    version: int = 1

    @property
    def category_names(self) -> list[str]:
        names = [rule.category for rule in self.rules]
        names.extend(TYPE_CATEGORIES.values())
        names.append(self.fallback)
        seen: dict[str, None] = {}
        for name in names:
            seen.setdefault(name, None)
        return list(seen)

    @property
    def product_categories(self) -> list[str]:
        """Kategorier en *vara* kan tillhöra.

        Pant, Rabatt och Avgifter sätts av radtypen och är bokföring, inte
        varutyper. Erbjuds de som val -- för modellen eller i en rullgardin --
        blir de förr eller senare valda: "Pant" ligger dessutom först i
        listan och blir den naturliga gissningen för ett namn ingen känner
        igen. Fallbacken hör inte hit heller; modellen har OKÄND för det, och
        två sätt att säga "vet ej" gör svaret sämre.
        """
        return [rule.category for rule in self.rules if rule.group != TYPE_GROUP]

    @property
    def groups(self) -> dict[str, str]:
        """Kategori -> grupp, inklusive de härledda och fallbacken."""
        mapping = {rule.category: rule.group for rule in self.rules}
        for category in TYPE_CATEGORIES.values():
            mapping[category] = TYPE_GROUP
        mapping[self.fallback] = FALLBACK_GROUP
        return mapping

    @property
    def group_names(self) -> list[str]:
        seen: dict[str, None] = {}
        for rule in self.rules:
            seen.setdefault(rule.group, None)
        seen.setdefault(TYPE_GROUP, None)
        seen.setdefault(FALLBACK_GROUP, None)
        return list(seen)

    def group_for(self, category: str) -> str:
        return self.groups.get(category, FALLBACK_GROUP)

    def classify(self, name_key: str, item_type: str) -> tuple[str, str]:
        """Returnera (kategori, källa).

        Bäst träff vinner, inte första träff: huvudordet i en sammansättning
        väger tyngre än förledet. Lika poäng bryts av ordningen i filen, så
        den ordningen ska gå från entydiga ord till svaga signaler.
        """
        type_category = TYPE_CATEGORIES.get(item_type)
        if type_category:
            return type_category, "type"

        best_rule = None
        best_score = 0
        for rule in self.rules:
            score = rule.match_score(name_key)
            if score > best_score:
                best_rule, best_score = rule, score
        if best_rule is not None:
            return best_rule.category, "rule"
        return self.fallback, "fallback"


def load_ruleset(path: Path | None = None) -> Ruleset:
    path = path or config.ensure_categories_file()
    if not path.exists():
        raise RuleError(f"Hittar ingen regelfil: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    fallback = str(data.get("fallback") or "Okategoriserat")
    version = int(data.get("version") or 1)
    rules: list[Rule] = []
    for entry in data.get("categories") or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            raise RuleError(f"Varje kategori måste ha ett 'name': {entry!r}")
        if version >= 2 and not entry.get("group"):
            raise RuleError(f"Kategorin {entry['name']!r} saknar 'group'")
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
        rules.append(
            Rule(
                str(entry["name"]),
                str(entry.get("group") or TYPE_GROUP),
                tuple(patterns),
            )
        )
    return Ruleset(tuple(rules), fallback, version)


def recategorize(conn: sqlite3.Connection, ruleset: Ruleset | None = None) -> dict[str, int]:
    """Sätt om kategori på alla rader. Ger statistik över utfallet."""
    ruleset = ruleset or load_ruleset()
    manual = store.overrides(conn)

    groups = ruleset.groups
    updates: list[tuple[str, str, str, int]] = []
    counts = {"total": 0, "manual": 0, "llm": 0, "rule": 0, "type": 0, "fallback": 0}
    for row in conn.execute("SELECT id, name_key, item_type FROM items"):
        counts["total"] += 1
        name = row["name_key"] or ""
        item_type = row["item_type"]
        if item_type in TYPE_CATEGORIES:
            category, source = TYPE_CATEGORIES[item_type], "type"
        elif name in manual:
            # Källan är "llm" eller "manual" -- inte bara "override". Först då
            # går ett fel att spåra till den som orsakade det.
            category, source = manual[name]
        else:
            category, source = ruleset.classify(name, item_type)
        counts[source] = counts.get(source, 0) + 1
        updates.append((category, groups.get(category, FALLBACK_GROUP), source, row["id"]))

    store.apply_categories(conn, updates)
    return counts


def unknown_items(
    conn: sqlite3.Connection,
    limit: int = 40,
    offset: int = 0,
    search: str | None = None,
) -> list[sqlite3.Row]:
    """Okategoriserade varor sorterade på hur mycket pengar de utgör.

    Undantagna varor utelämnas. Annars hade en dold present visat sitt namn
    här, i huvudvyns kvalitetslista -- vilket vore hela poängen förlorad.
    """
    clauses = ["category_source = 'fallback'", "excluded = 0"]
    params: list = []
    if search:
        clauses.append("(name_key LIKE ? OR name LIKE ?)")
        needle = f"%{search.lower()}%"
        params.extend([needle, f"%{search}%"])

    return list(
        conn.execute(
            f"""
            SELECT name_key,
                   MIN(name)            AS example_name,
                   COUNT(*)             AS times,
                   SUM(line_total_ore)  AS total_ore
            FROM items
            WHERE {" AND ".join(clauses)}
            GROUP BY name_key
            ORDER BY total_ore DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        )
    )


def unknown_total(conn: sqlite3.Connection, search: str | None = None) -> int:
    """Antal distinkta okategoriserade varunamn, för pagineringen."""
    clauses = ["category_source = 'fallback'", "excluded = 0"]
    params: list = []
    if search:
        clauses.append("(name_key LIKE ? OR name LIKE ?)")
        needle = f"%{search.lower()}%"
        params.extend([needle, f"%{search}%"])
    row = conn.execute(
        f"SELECT COUNT(DISTINCT name_key) AS n FROM items WHERE {' AND '.join(clauses)}",
        params,
    ).fetchone()
    return row["n"] or 0


def unknown_groups(
    conn: sqlite3.Connection,
    limit: int = 25,
    min_items: int = 2,
    prefix_length: int = 4,
) -> list[dict]:
    """Gruppera okategoriserat på gemensamt ordprefix.

    Att gruppera på första *ordet* fungerar dåligt på svenska: sammansättningar
    skrivs ihop, så GRILLREMSA, GRILLKORV och GRILLKOL har inget gemensamt
    förstaord trots att de uppenbart hör ihop. Ett teckenprefix fångar dem,
    och eftersom huvudordet oftast står först i kvittonamnet blir grupperna
    användbara: "gril" samlar grillsaker, "mjöl" mjölksorterna.

    Grupperna är ett förslag, inte ett facit -- därför följer exempelnamn med
    så valet kan synas efter innan det appliceras.
    """
    groups: dict[str, dict] = {}
    for row in conn.execute(
        """
        SELECT name_key, MIN(name) AS example_name,
               COUNT(*) AS times, SUM(line_total_ore) AS total_ore
        FROM items
        WHERE category_source = 'fallback' AND excluded = 0 AND name_key <> ''
        GROUP BY name_key
        """
    ):
        head = (row["name_key"] or "")[:prefix_length]
        if len(head) < prefix_length:
            continue
        group = groups.setdefault(
            head,
            {"prefix": head, "name_keys": [], "examples": [], "times": 0, "total_ore": 0},
        )
        group["name_keys"].append(row["name_key"])
        if len(group["examples"]) < 5:
            group["examples"].append(row["example_name"])
        group["times"] += row["times"]
        group["total_ore"] += row["total_ore"] or 0

    usable = [g for g in groups.values() if len(g["name_keys"]) >= min_items]
    usable.sort(key=lambda g: g["total_ore"], reverse=True)
    return usable[:limit]


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
        WHERE item_type IN ('product', 'return') AND excluded = 0
        """
    ).fetchone()
    total = row["total_ore"] or 0
    unknown = row["unknown_ore"] or 0
    return {
        "total_ore": total,
        "unknown_ore": unknown,
        "items": row["items"] or 0,
        "unknown_items": row["unknown_items"] or 0,
        # None, inte 1.0: "100 % kategoriserat" när det inte finns några rader
        # är en osann uppgift som döljer att något är fel.
        "covered_share": None if total == 0 else max(0.0, 1 - unknown / total),
    }
