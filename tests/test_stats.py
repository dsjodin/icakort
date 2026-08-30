import pytest

from icakort import categorize, stats, store, sync
from icakort.normalize import normalize_receipt


@pytest.fixture
def conn(tmp_path, raw_receipt):
    conn = store.connect(tmp_path / "test.db")
    receipt = normalize_receipt(raw_receipt["receipt"], raw_receipt["list_entry"])
    store.save_receipt(conn, receipt)
    categorize.recategorize(conn)
    yield conn
    conn.close()


def test_summary(conn):
    head = stats.summary(conn)
    assert head["receipts"] == 1
    assert head["items"] == 6
    assert head["total_ore"] == 17943
    assert head["avg_receipt_ore"] == 17943


def test_categories_sum_to_total(conn):
    """Varje rad har en kategori, så kategorierna måste summera till totalen."""
    head = stats.summary(conn)
    rows = stats.by_category(conn)
    assert sum(row["total_ore"] for row in rows) == head["total_ore"]


def test_monthly_and_store_breakdown(conn):
    assert stats.monthly(conn) == [
        {"month": "2026-03", "total_ore": 17943, "receipts": 1}
    ]
    assert stats.by_store(conn)[0]["store"] == "ICA Kvantum Testköping"


def test_filters_narrow_the_result(conn):
    assert stats.summary(conn, stats.Filters(date_from="2026-04-01"))["receipts"] == 0


def test_the_two_levels_narrow_differently(conn):
    """Gruppen samlar löven -- det är hela poängen med två nivåer.

    Kaffet och colan är olika kategorier men samma grupp: filtrerar man på
    gruppen får man båda, filtrerar man på lövet bara det ena.
    """
    assert stats.summary(conn, stats.Filters(group="Dryck"))["total_ore"] == 4990 + 5590
    assert stats.summary(conn, stats.Filters(category="Kaffe & te"))["total_ore"] == 4990
    assert stats.summary(conn, stats.Filters(category="Läsk & vatten"))["total_ore"] == 5590


def test_group_totals_match_category_totals(conn):
    """Varje rad har både nivåerna, så summorna måste vara identiska."""
    by_group = sum(row["total_ore"] for row in stats.by_group(conn))
    by_category = sum(row["total_ore"] for row in stats.by_category(conn))
    assert by_group == by_category == stats.summary(conn)["total_ore"]


def test_top_items_excludes_discount_rows(conn):
    names = [row["name"] for row in stats.top_items(conn)]
    assert "Kuponger" not in names
    assert "KAFFE MELLANROST 450G" in names


def test_price_history(conn):
    points = stats.price_history(conn, "banan eko")
    assert points == [
        {
            "date": "2026-03-14",
            "store": "ICA Kvantum Testköping",
            "unit_price_ore": 2490,
            "unit": "kg",
            "quantity": 0.712,
            "line_total_ore": 1773,
        }
    ]


def test_saving_twice_is_idempotent(conn, raw_receipt):
    receipt = normalize_receipt(raw_receipt["receipt"], raw_receipt["list_entry"])
    store.save_receipt(conn, receipt)
    store.save_receipt(conn, receipt)
    head = stats.summary(conn)
    assert head["receipts"] == 1
    assert head["items"] == 6


def test_verify_finds_no_mismatch(conn):
    assert sync.verify(conn) == []


def test_each_group_appears_once(conn):
    """Aliaset "category" krockar med kolumnen items.category i GROUP BY.

    SQLite låter kolumnen vinna, så grupperingen skedde på lövet medan
    gruppnamnet visades -- varje grupp dök upp en gång per löv. Summorna blev
    ändå rätt, vilket är varför bara den här kontrollen fångar det.
    """
    groups = [row["category"] for row in stats.by_group(conn)]
    assert len(groups) == len(set(groups)), groups

    categories = [row["category"] for row in stats.by_category(conn)]
    assert len(categories) == len(set(categories)), categories
