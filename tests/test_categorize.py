import pytest

from icakort import categorize, config, store
from icakort.normalize import normalize_receipt

# Reglerna som följer med i paketet -- det är dem användaren får vid första körningen.
RULES = config.DEFAULT_CATEGORIES


@pytest.fixture
def conn(tmp_path, raw_receipt):
    conn = store.connect(tmp_path / "test.db")
    receipt = normalize_receipt(raw_receipt["receipt"], raw_receipt["list_entry"])
    store.save_receipt(conn, receipt)
    yield conn
    conn.close()


@pytest.fixture
def ruleset():
    return categorize.load_ruleset(RULES)


@pytest.mark.parametrize(
    "name_key,expected",
    [
        ("mjölk mellan", "Mejeri & ägg"),
        ("banan eko", "Frukt & grönt"),
        ("kaffe mellanrost", "Dryck"),
        ("coca cola", "Dryck"),
        ("prylburk xyz", "Okategoriserat"),
    ],
)
def test_rules_classify_products(ruleset, name_key, expected):
    assert ruleset.classify(name_key, "product")[0] == expected


def test_row_type_beats_name(ruleset):
    """Pant och rabatt är vad radtypen säger, oavsett vad raden heter."""
    assert ruleset.classify("pant mjölk", "deposit") == ("Pant", "type")
    assert ruleset.classify("banan", "discount") == ("Rabatt", "type")


def test_recategorize_marks_unknown(conn, ruleset):
    counts = categorize.recategorize(conn, ruleset)
    assert counts["total"] == 6
    assert counts["fallback"] == 1          # PRYLBURK XYZ

    unknown = categorize.unknown_items(conn)
    assert [row["example_name"] for row in unknown] == ["PRYLBURK XYZ"]


def test_override_beats_rules(conn, ruleset):
    categorize.recategorize(conn, ruleset)
    store.set_override(conn, "prylburk xyz", "Städ & hushåll")
    counts = categorize.recategorize(conn, ruleset)

    assert counts["fallback"] == 0
    assert counts["override"] == 1
    row = conn.execute(
        "SELECT category FROM items WHERE name_key = 'prylburk xyz'"
    ).fetchone()
    assert row["category"] == "Städ & hushåll"


def test_coverage_reflects_unknown_money(conn, ruleset):
    categorize.recategorize(conn, ruleset)
    cov = categorize.coverage(conn)
    assert cov["unknown_ore"] == 2500       # PRYLBURK XYZ
    assert 0 < cov["covered_share"] < 1


def test_bad_rule_file_is_reported(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("categories:\n  - match: ['x']\n", encoding="utf-8")
    with pytest.raises(categorize.RuleError):
        categorize.load_ruleset(path)


@pytest.mark.parametrize(
    "literal,name,expected",
    [
        # Sammansättningar ska fångas åt båda hållen...
        ("mjölk", "havremjölk", True),
        ("kyckling", "kycklingfilé", True),
        ("bär", "hallonbär", True),
        # ...men en träff mitt inne i ett ord är en slump.
        ("ros", "kaffe mellanrost", False),
        ("ris", "turist", False),
    ],
)
def test_literal_matches_at_word_boundaries(literal, name, expected):
    assert bool(categorize.compile_literal(literal).search(name)) is expected


@pytest.mark.parametrize(
    "name_key,expected",
    [
        ("kaffe mellanrost", "Dryck"),          # inte Blommor via "ros"
        ("diskmedel citron", "Städ & hushåll"),  # inte Frukt & grönt via "citron"
        ("kycklingfilé", "Kött & chark"),
        ("ostbågar", "Godis & snacks"),          # inte Mejeri via "ost"
        ("getost", "Mejeri & ägg"),
        ("ost herrgård", "Mejeri & ägg"),
        ("jordnötssmör", "Skafferi"),            # inte Mejeri via "smör"
        ("krukväxt basilika", "Blommor & växter"),
        ("bärkasse", "Städ & hushåll"),
    ],
)
def test_ambiguous_names_land_in_the_right_category(ruleset, name_key, expected):
    assert ruleset.classify(name_key, "product")[0] == expected
