"""Prisanalysen, testad mot data där rätt svar är känt i förväg.

Den viktigaste egenskapen är att indexet mäter *pris*, inte *vad du köpte*.
Ett byte till en dyrare vara är inte inflation, och det testet är det som
skiljer en användbar siffra från en vilseledande.
"""

import pytest

from icakort import stats, store
from icakort.normalize import Item, Receipt


def _receipt(key: str, date: str, store_name: str, rows: list[tuple[str, int, int]]):
    """rows: (namn, styckpris i ören, antal)."""
    items = []
    for line_no, (name, price, quantity) in enumerate(rows):
        items.append(
            Item(
                line_no=line_no,
                item_type="product",
                name=name,
                name_key=name.lower(),
                section="Varor",
                quantity=float(quantity),
                unit="st",
                unit_price_ore=price,
                amount_ore=price * quantity,
            )
        )
    total = sum(item.line_total_ore for item in items)
    return Receipt(key, date, store_name, None, total, items)


@pytest.fixture
def conn(tmp_path):
    conn = store.connect(tmp_path / "priser.db")
    yield conn
    conn.close()


def _fill(conn, receipts):
    for receipt in receipts:
        store.save_receipt(conn, receipt)


# ---------------------------------------------------------------------------
# basket_index
# ---------------------------------------------------------------------------


def test_a_doubled_price_shows_up_in_the_index(conn):
    _fill(conn, [
        _receipt(f"a{i}", f"2024-0{i+1}-01", "ICA", [("Mjölk", 1000, 1)]) for i in range(4)
    ] + [
        _receipt(f"b{i}", f"2025-0{i+1}-01", "ICA", [("Mjölk", 2000, 1)]) for i in range(4)
    ])

    index = stats.basket_index(conn)
    assert [row["year"] for row in index] == ["2024", "2025"]
    assert index[0]["index"] == 100.0
    assert index[1]["index"] == pytest.approx(200.0)


def test_swapping_to_a_pricier_product_is_not_inflation(conn):
    """Blandningsskiftet är hela fällan: samma pengar, dyrare vara, noll index.

    Kaffet kostar lika mycket båda åren. Att du dessutom börjat köpa lax i
    stället för korv ska inte räknas som att priserna stigit.
    """
    _fill(conn, [
        _receipt(f"a{i}", f"2024-0{i+1}-01", "ICA", [("Kaffe", 5000, 1), ("Korv", 3000, 1)])
        for i in range(4)
    ] + [
        _receipt(f"b{i}", f"2025-0{i+1}-01", "ICA", [("Kaffe", 5000, 1), ("Lax", 15000, 1)])
        for i in range(4)
    ])

    index = stats.basket_index(conn)
    # Bara kaffet finns i båda åren, och det står stilla.
    assert index[1]["index"] == pytest.approx(100.0)
    assert index[1]["matched"] == 1


def test_the_index_weights_by_spend(conn):
    """Vardagsvaran ska väga tyngre än den man köper någon enstaka gång.

    Saffranets ökning ligger medvetet innanför trimningsfönstret, annars
    hade den kastats bort och testet passerat utan att viktningen prövats.
    """
    _fill(conn, [
        # Mjölk: 100 kr per kvitto. Saffran: 5 kr.
        _receipt("a1", "2024-01-01", "ICA", [("Mjölk", 1000, 10), ("Saffran", 500, 1)]),
        _receipt("a2", "2024-06-01", "ICA", [("Mjölk", 1000, 10), ("Saffran", 500, 1)]),
        # Mjölken står still, saffran +80 %.
        _receipt("b1", "2025-01-01", "ICA", [("Mjölk", 1000, 10), ("Saffran", 900, 1)]),
        _receipt("b2", "2025-06-01", "ICA", [("Mjölk", 1000, 10), ("Saffran", 900, 1)]),
    ])

    index = stats.basket_index(conn)
    # Oviktat hade det blivit sqrt(1 × 1,8) = 134. Med utgiftsvikter drar
    # mjölken ner det till knappt 103.
    assert index[1]["index"] == pytest.approx(102.8, abs=0.5)


def test_an_absurd_price_relative_is_trimmed_away(conn):
    """Mer än en fördubbling åt något håll är oftare ett fel än ett pris.

    En enhetsförväxling eller en felläst rad ska inte kunna kasta om hela
    indexet.
    """
    _fill(conn, [
        _receipt("a1", "2024-01-01", "ICA", [("Ris", 2000, 1), ("Trasig", 1000, 1)]),
        _receipt("a2", "2024-06-01", "ICA", [("Ris", 2000, 1), ("Trasig", 1000, 1)]),
        _receipt("b1", "2025-01-01", "ICA", [("Ris", 2000, 1), ("Trasig", 90000, 1)]),
        _receipt("b2", "2025-06-01", "ICA", [("Ris", 2000, 1), ("Trasig", 90000, 1)]),
    ])

    index = stats.basket_index(conn)
    assert index[1]["index"] == pytest.approx(100.0)
    assert index[1]["matched"] == 1        # bara riset räknades


def test_weights_come_from_the_base_period(conn):
    """En vara får inte tyngre vikt bara för att den stigit i pris.

    Vägde vi med den senare periodens utgift skulle en prisökning förstora
    sin egen vikt och driva upp indexet en andra gång.
    """
    _fill(conn, [
        # Startläget: lika stor utgift på båda varorna, 100 kr var.
        _receipt("a1", "2024-01-01", "ICA", [("Bas", 1000, 10), ("Dyr", 10000, 1)]),
        _receipt("a2", "2024-06-01", "ICA", [("Bas", 1000, 10), ("Dyr", 10000, 1)]),
        # Dyr +90 %, Bas står still.
        _receipt("b1", "2025-01-01", "ICA", [("Bas", 1000, 10), ("Dyr", 19000, 1)]),
        _receipt("b2", "2025-06-01", "ICA", [("Bas", 1000, 10), ("Dyr", 19000, 1)]),
    ])

    index = stats.basket_index(conn)
    # Basperiodvikter är lika: sqrt(1 × 1,9) = 138.
    # Med den senare periodens vikter hade Dyr vägt 1,9 gånger tyngre bara
    # för att den stigit, och indexet landat kring 152.
    assert index[1]["index"] == pytest.approx(137.8, abs=1.0)


def test_an_empty_database_gives_no_index(conn):
    assert stats.basket_index(conn) == []


# ---------------------------------------------------------------------------
# price_changes
# ---------------------------------------------------------------------------


def test_price_changes_reports_the_rise(conn):
    _fill(conn, [
        _receipt("a1", "2024-01-01", "ICA", [("Smör", 4000, 1)]),
        _receipt("a2", "2024-02-01", "ICA", [("Smör", 4000, 1)]),
        _receipt("a3", "2025-01-01", "ICA", [("Smör", 6000, 1)]),
        _receipt("a4", "2025-02-01", "ICA", [("Smör", 6000, 1)]),
    ])

    changes = stats.price_changes(conn)
    smor = next(row for row in changes if row["name_key"] == "smör")
    assert smor["first_ore"] == 4000
    assert smor["last_ore"] == 6000
    assert smor["change_share"] == pytest.approx(0.5)
    assert smor["observations"] == 4


def test_too_few_observations_are_left_out(conn):
    """Två köp är inte en trend."""
    _fill(conn, [
        _receipt("a1", "2024-01-01", "ICA", [("Tryffel", 50000, 1)]),
        _receipt("a2", "2025-01-01", "ICA", [("Tryffel", 99000, 1)]),
    ])
    assert stats.price_changes(conn) == []


def test_units_are_never_mixed(conn):
    """Samma namn med olika enhet är olika priser och får inte jämföras."""
    rows = []
    for i in range(3):
        r = _receipt(f"kg{i}", f"2024-0{i+1}-01", "ICA", [("Banan", 2000, 1)])
        r.items[0].unit = "kg"
        rows.append(r)
    for i in range(3):
        r = _receipt(f"st{i}", f"2025-0{i+1}-01", "ICA", [("Banan", 400, 1)])
        r.items[0].unit = "st"
        rows.append(r)
    _fill(conn, rows)

    bananer = [row for row in stats.price_changes(conn) if row["name_key"] == "banan"]

    # Två separata rader, en per enhet -- och ingen av dem visar det falska
    # prisras på 80 % som en sammanslagning hade gett.
    assert sorted(row["unit"] for row in bananer) == ["kg", "st"]
    assert all(row["change_share"] == 0 for row in bananer)


# ---------------------------------------------------------------------------
# store_prices
# ---------------------------------------------------------------------------


def test_store_comparison_finds_the_spread(conn):
    _fill(conn, [
        _receipt("m1", "2025-01-01", "Maxi", [("Kaffe", 5000, 1)]),
        _receipt("m2", "2025-02-01", "Maxi", [("Kaffe", 5000, 1)]),
        _receipt("n1", "2025-03-01", "Nära", [("Kaffe", 7500, 1)]),
        _receipt("n2", "2025-04-01", "Nära", [("Kaffe", 7500, 1)]),
    ])

    rows = stats.store_prices(conn)
    assert len(rows) == 1
    assert rows[0]["cheapest_store"] == "Maxi"
    assert rows[0]["dearest_store"] == "Nära"
    assert rows[0]["spread_share"] == pytest.approx(0.5)


def test_store_comparison_does_not_compare_across_years(conn):
    """Utan tidskontroll hade det här sett ut som en butiksskillnad."""
    _fill(conn, [
        _receipt("m1", "2021-01-01", "Maxi", [("Kaffe", 5000, 1)]),
        _receipt("m2", "2021-02-01", "Maxi", [("Kaffe", 5000, 1)]),
        _receipt("n1", "2025-01-01", "Nära", [("Kaffe", 9000, 1)]),
        _receipt("n2", "2025-02-01", "Nära", [("Kaffe", 9000, 1)]),
    ])
    assert stats.store_prices(conn) == []
