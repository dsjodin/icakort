"""Taxonomin och omstarten av kategoriseringen."""

import sqlite3

import pytest

from icakort import categorize, config, store
from icakort.normalize import normalize_receipt


@pytest.fixture
def ruleset():
    return categorize.load_ruleset(config.DEFAULT_CATEGORIES)


def test_every_category_belongs_to_a_group(ruleset):
    assert ruleset.version >= 2
    for rule in ruleset.rules:
        assert rule.group, rule.category


def test_group_count_stays_chartable(ruleset):
    """Liggande staplar tål listan; den staplade kolumnen viker ihop själv.

    Det som faktiskt begränsar är den staplade månadsvyn, och den kapar till
    fem plus Övrigt oavsett hur många grupper som finns. Taket här är därför
    en rimlighetsspärr mot att taxonomin växer okontrollerat, inte en
    ritteknisk gräns.
    """
    assert len(ruleset.group_names) <= 14


def test_leaf_names_are_unique(ruleset):
    names = [rule.category for rule in ruleset.rules]
    assert len(names) == len(set(names))


def test_derived_rows_get_their_own_group(ruleset):
    """Pant och rabatt är inte varor, och en rabatt som nettas mot
    varuutgifter försvinner ur diagrammet."""
    for category in categorize.TYPE_CATEGORIES.values():
        assert ruleset.group_for(category) == categorize.TYPE_GROUP
    assert categorize.TYPE_GROUP not in {rule.group for rule in ruleset.rules if rule.category != "Pant"}


def test_uncategorised_is_visible_on_its_own(ruleset):
    """Göms det bland blommor och djurmat ser statistiken mer komplett ut
    än den är."""
    assert ruleset.group_for(ruleset.fallback) == categorize.FALLBACK_GROUP
    assert categorize.FALLBACK_GROUP != "Övrigt"


def test_a_category_without_a_group_is_rejected(tmp_path):
    path = tmp_path / "trasig.yaml"
    path.write_text("version: 2\ncategories:\n  - name: Utan grupp\n", encoding="utf-8")
    with pytest.raises(categorize.RuleError, match="group"):
        categorize.load_ruleset(path)


@pytest.mark.parametrize(
    "name_key,category,group",
    [
        # Förvaringsform är inte varutyp.
        ("fryst lax", "Fisk & skaldjur", "Kött & fisk"),
        ("glass strut", "Glass", "Godis & snacks"),
        ("fryst pizza", "Pizza", "Färdigmat"),
        # Skafferiet är uppdelat.
        ("pasta penne", "Pasta & ris", "Skafferi"),
        ("vetemjöl", "Mjöl & bakning", "Skafferi"),
        ("svartpeppar", "Kryddor", "Skafferi"),
        ("olivolja", "Oljor & vinäger", "Skafferi"),
        # Mejeri är uppdelat.
        ("ost herrgård", "Ost", "Mejeri & ägg"),
        ("mjölk mellan", "Mjölk & fil", "Mejeri & ägg"),
        # Nya kategorier.
        ("blöjor storlek", "Blöjor & barn", "Hygien & barn"),
        ("kycklingfilé", "Fågel", "Kött & fisk"),
        # Gamla fallgropar ska stå kvar lösta.
        ("kaffe mellanrost", "Kaffe & te", "Dryck"),
        ("diskmedel citron", "Städ & rengöring", "Hushåll"),
    ],
)
def test_taxonomy_places_items_sensibly(ruleset, name_key, category, group):
    assert ruleset.classify(name_key, "product")[0] == category
    assert ruleset.group_for(category) == group


# ---------------------------------------------------------------------------
# Omstarten
# ---------------------------------------------------------------------------


def test_reset_keeps_receipts_and_items_untouched(tmp_path, raw_receipt):
    conn = store.connect(tmp_path / "test.db")
    receipt = normalize_receipt(raw_receipt["receipt"], raw_receipt["list_entry"])
    store.save_receipt(conn, receipt, owner_key="konto-a", owner_name="Alex")
    store.set_excluded(conn, name_key="prylburk xyz", excluded=True)
    store.set_override(conn, "banan eko", "Gammal Kategori")
    categorize.recategorize(conn)

    before = conn.execute(
        "SELECT COUNT(*) AS receipts, "
        "(SELECT COUNT(*) FROM items) AS items, "
        "(SELECT SUM(line_total_ore) FROM items) AS total, "
        "(SELECT COUNT(*) FROM items WHERE excluded = 1) AS hidden, "
        "(SELECT COUNT(*) FROM receipts WHERE owner_key IS NOT NULL) AS owned "
        "FROM receipts"
    ).fetchone()

    removed = store.reset_categorisation(conn)

    after = conn.execute(
        "SELECT COUNT(*) AS receipts, "
        "(SELECT COUNT(*) FROM items) AS items, "
        "(SELECT SUM(line_total_ore) FROM items) AS total, "
        "(SELECT COUNT(*) FROM items WHERE excluded = 1) AS hidden, "
        "(SELECT COUNT(*) FROM receipts WHERE owner_key IS NOT NULL) AS owned "
        "FROM receipts"
    ).fetchone()

    assert removed["overrides"] == 1
    assert dict(after) == dict(before)          # inget av datan rörd
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM items WHERE category IS NOT NULL"
    ).fetchone()["n"] == 0
    conn.close()


def test_recategorize_writes_both_levels(tmp_path, raw_receipt):
    conn = store.connect(tmp_path / "test.db")
    store.save_receipt(conn, normalize_receipt(raw_receipt["receipt"], raw_receipt["list_entry"]))
    categorize.recategorize(conn)

    rows = conn.execute(
        "SELECT category, category_group FROM items WHERE name_key = 'mjölk mellan'"
    ).fetchall()
    assert rows
    assert rows[0]["category"] == "Mjölk & fil"
    assert rows[0]["category_group"] == "Mejeri & ägg"
    conn.close()


# ---------------------------------------------------------------------------
# Regelfilens versionsuppgradering
# ---------------------------------------------------------------------------


def test_an_older_rules_file_is_upgraded_and_backed_up(tmp_path, monkeypatch):
    """Utan uppgraderingen når en ny taxonomi aldrig en befintlig installation."""
    monkeypatch.setenv("ICAKORT_DATA_DIR", str(tmp_path))
    path = tmp_path / "categories.yaml"
    path.write_text("fallback: Okategoriserat\ncategories: []\n", encoding="utf-8")

    config.ensure_categories_file()

    assert (tmp_path / "categories.v1.bak").exists()          # gamla sparad
    assert config._file_version(path) >= 2                    # nya på plats
    assert categorize.load_ruleset(path).rules                # och den laddar


def test_a_current_rules_file_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("ICAKORT_DATA_DIR", str(tmp_path))
    path = tmp_path / "categories.yaml"
    path.write_text("version: 99\nfallback: Eget\ncategories: []\n", encoding="utf-8")

    config.ensure_categories_file()

    assert not (tmp_path / "categories.v99.bak").exists()
    assert categorize.load_ruleset(path).fallback == "Eget"


# ---------------------------------------------------------------------------
# Rangordningen: huvudordet vinner
#
# Svenska sammansättningar sätter huvudordet sist. Innan rangordningen fanns
# avgjorde filordningen, och ett förled kunde slå huvudordet: "korvbröd" blev
# chark, "citronläsk" blev frukt.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name_key,category",
    [
        ("korvbröd", "Bröd"),                          # bröd (slut) slår korv (början)
        ("citronläsk", "Läsk & vatten"),               # läsk slår citron
        ("kycklingbuljong", "Konserver & torrvaror"),  # buljong slår kyckling
        ("päroncider", "Öl & cider"),                  # cider slår päron
        ("loka citron", "Läsk & vatten"),              # varumärket slår smakordet
        ("ramlösa naturell", "Läsk & vatten"),
    ],
)
def test_reported_misclassifications_are_fixed(ruleset, name_key, category):
    assert ruleset.classify(name_key, "product")[0] == category


@pytest.mark.parametrize(
    "name_key,category",
    [
        # Förledet ska fortfarande vinna när huvudordet inte är någon regel.
        ("kycklingfilé", "Fågel"),
        # Huvudordet vinner även där filordningen förr råkade ge rätt svar.
        ("havremjölk", "Mjölk & fil"),
        ("bärkasse", "Förvaring & påsar"),
        # Hela ord väger tyngst.
        ("mjölk mellan", "Mjölk & fil"),
        ("citron", "Frukt"),                 # ensamt smakord är fortfarande frukt
        ("banan", "Frukt"),
    ],
)
def test_ranking_does_not_break_what_worked(ruleset, name_key, category):
    assert ruleset.classify(name_key, "product")[0] == category


def test_score_ranks_by_position_in_the_word():
    from icakort.categorize import _score_span

    # "korvbröd": korv i början, bröd i slutet, hela ordet.
    assert _score_span("korvbröd", 0, 4) == categorize.SCORE_MODIFIER
    assert _score_span("korvbröd", 4, 8) == categorize.SCORE_HEAD
    assert _score_span("korvbröd", 0, 8) == categorize.SCORE_WHOLE_WORD
    # Andra ordet i ett namn med mellanslag.
    assert _score_span("loka citron", 5, 11) == categorize.SCORE_WHOLE_WORD
    assert categorize.SCORE_WHOLE_WORD > categorize.SCORE_HEAD > categorize.SCORE_MODIFIER


def test_a_tie_is_broken_by_file_order(ruleset):
    """Två hela ord ger samma poäng -- då avgör ordningen, som därför måste
    gå från entydiga ord till svaga signaler."""
    assert ruleset.classify("diskmedel citron", "product")[0] == "Städ & rengöring"


# ---------------------------------------------------------------------------
# Källan till en kategori
# ---------------------------------------------------------------------------


def test_the_source_distinguishes_claude_from_a_manual_fix(tmp_path, raw_receipt):
    conn = store.connect(tmp_path / "test.db")
    store.save_receipt(conn, normalize_receipt(raw_receipt["receipt"], raw_receipt["list_entry"]))
    store.set_override(conn, "prylburk xyz", "Kryddor", source="llm")
    store.set_override(conn, "banan eko", "Frukt", source="manual")
    counts = categorize.recategorize(conn)

    assert counts["llm"] == 1
    assert counts["manual"] == 1
    sources = dict(conn.execute(
        "SELECT name_key, category_source FROM items WHERE name_key IN "
        "('prylburk xyz', 'banan eko')"
    ).fetchall())
    assert sources == {"prylburk xyz": "llm", "banan eko": "manual"}
    conn.close()


def test_releasing_claudes_answers_keeps_manual_fixes(tmp_path, raw_receipt):
    """En rättad regel ska få gälla igen -- men inte på bekostnad av
    rättningar man gjort själv."""
    conn = store.connect(tmp_path / "test.db")
    store.save_receipt(conn, normalize_receipt(raw_receipt["receipt"], raw_receipt["list_entry"]))
    store.set_override(conn, "prylburk xyz", "Kryddor", source="llm")
    store.set_override(conn, "banan eko", "Godis", source="manual")

    removed = store.clear_model_overrides(conn)
    categorize.recategorize(conn)

    assert removed == 1
    remaining = store.overrides(conn)
    assert remaining == {"banan eko": ("Godis", "manual")}
    conn.close()
