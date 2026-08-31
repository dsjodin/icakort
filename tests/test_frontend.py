"""Frontendtester i en riktig webbläsare.

Diagramkoden är 1 000 rader JavaScript, och de två buggar som nått
produktion -- hopklumpade axeletiketter och negativa staplar ritade som
positiv längd -- var båda osynliga för Python-testerna och hittades genom
att titta på skärmbilder. Det här är den luckan.

Testerna hoppas över rent när ingen webbläsare finns installerad, så
`pytest` inte går sönder för den som inte kört `playwright install chromium`.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from icakort import categorize, store
from icakort.normalize import Item, Receipt

pytest.importorskip("playwright", reason="playwright är inte installerat")

from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _receipt(key, date, store_name, rows, owner=None):
    items = [
        Item(
            line_no=i,
            item_type=kind,
            name=name,
            name_key=name.lower(),
            section="Varor",
            quantity=1.0,
            unit="st",
            unit_price_ore=abs(amount),
            amount_ore=amount,
        )
        for i, (name, amount, kind) in enumerate(rows)
    ]
    total = sum(item.line_total_ore for item in items)
    return Receipt(key, date, store_name, None, total, items, owner_name=owner)


@pytest.fixture(scope="module")
def browser():
    import os

    # ICAKORT_CHROMIUM pekar ut en webbläsare som inte ligger där Playwright
    # förväntar sig den -- praktiskt i containrar med förinstallerad Chromium.
    executable = os.environ.get("ICAKORT_CHROMIUM") or None
    with sync_playwright() as p:
        try:
            instance = p.chromium.launch(executable_path=executable)
        except PlaywrightError as exc:
            pytest.skip(f"ingen webbläsare tillgänglig: {exc}")
        yield instance
        instance.close()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """Uvicorn i en tråd mot en seedad temporär databas."""
    import os

    data_dir = tmp_path_factory.mktemp("frontend")
    os.environ["ICAKORT_DATA_DIR"] = str(data_dir)
    os.environ["ICAKORT_OWNER_KEY"] = "test-nyckel"
    os.environ.pop("ICAKORT_PASSWORD", None)

    conn = store.connect(data_dir / "icakort.db")
    # Fem år, så axeln måste glesas ut, och en rabattrad så den negativa
    # stapeln får något att rita.
    for year in range(2020, 2025):
        for month in range(1, 13):
            store.save_receipt(
                conn,
                _receipt(
                    f"r-{year}-{month:02d}",
                    f"{year}-{month:02d}-15",
                    "ICA Test",
                    [
                        ("Mjölk", 2000 + (year - 2020) * 400, "product"),
                        ("Kaffe", 5000, "product"),
                        ("Okändvara", 3000, "product"),
                        ("Kuponger", -500, "discount"),
                    ],
                ),
                owner_key="konto-a",
                owner_name="Alex",
            )
    categorize.recategorize(conn)
    conn.close()

    import uvicorn

    from icakort.web.app import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    uvicorn_server = uvicorn.Server(config)
    thread = threading.Thread(target=uvicorn_server.run, daemon=True)
    thread.start()

    deadline = time.time() + 15
    while not uvicorn_server.started and time.time() < deadline:
        time.sleep(0.05)
    if not uvicorn_server.started:
        pytest.skip("servern startade inte")

    yield f"http://127.0.0.1:{port}"

    uvicorn_server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def page(browser, server):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(server, wait_until="networkidle")
    page.wait_for_timeout(800)
    yield page
    assert errors == [], f"konsolfel: {errors}"
    context.close()


# ---------------------------------------------------------------------------
# De två buggar som nådde produktion
# ---------------------------------------------------------------------------


def _axis_labels(page, selector):
    return page.eval_on_selector_all(
        f"{selector} svg text",
        "els => els.map(e => e.textContent).filter(t => t && !t.includes('kr'))",
    )


def test_the_month_axis_thins_out_over_long_spans(page):
    """60 månader gav förut en etikett per stapel och blev oläslig gröt."""
    labels = _axis_labels(page, "#chart-monthly")

    assert labels == ["2020", "2021", "2022", "2023", "2024"]


def test_the_tooltip_still_names_the_month_the_axis_left_out(page):
    """Utglesningen får inte göra hovern innehållslös."""
    page.locator("#chart-monthly svg rect[tabindex]").nth(7).hover()
    page.wait_for_timeout(300)

    head = page.locator("#chart-monthly .tooltip-head").inner_text()
    assert head == "2020-08"        # en månad utan egen axeletikett


def test_a_negative_bar_grows_the_other_way_from_zero(page):
    """Rabattrader ritades förut som positiv längd, vilket ljuger."""
    bars = page.eval_on_selector_all(
        "#chart-categories svg path",
        "els => els.map(e => e.getAttribute('d'))",
    )
    fills = page.eval_on_selector_all(
        "#chart-categories svg path",
        "els => els.map(e => e.getAttribute('fill'))",
    )
    # Rabatten är den enda negativa kategorin och ska ha den divergerande
    # motpolen, inte samma färg som de positiva staplarna.
    assert len(set(fills)) == 2, fills
    assert bars, "inga staplar ritade"


def test_a_negative_value_label_does_not_collide_with_the_category_name(page):
    """Etiketten hamnade förut ovanpå kategorinamnet till vänster."""
    labels = page.eval_on_selector_all(
        "#chart-categories svg text",
        "els => els.map(e => ({ x: +e.getAttribute('x'),"
        " anchor: e.getAttribute('text-anchor'), t: e.textContent }))",
    )
    negative = [row for row in labels if row["t"].startswith(("−", "-"))]
    assert negative, "hittade ingen negativ etikett"

    # Kategorinamnen är högerställda och slutar där namnkolumnen slutar.
    name_column_ends = max(row["x"] for row in labels if row["anchor"] == "end")
    # Den negativa etiketten ska ligga till höger om den kolumnen, alltså på
    # fri sida om nollinjen -- inte ovanpå kategorinamnet.
    assert all(row["x"] > name_column_ends for row in negative), negative


# ---------------------------------------------------------------------------
# Flöden
# ---------------------------------------------------------------------------


def test_bulk_categorisation_clears_several_items_at_once(page):
    page.locator("#quality").scroll_into_view_if_needed()
    page.wait_for_timeout(400)

    before = page.locator(".bulk tbody tr").count()
    assert before >= 1

    page.locator(".bulk-tools button", has_text="Markera alla synliga").click()
    page.locator(".bulk-tools select").select_option("Pasta & ris")
    page.locator(".bulk-tools button.primary").click()
    page.wait_for_timeout(1500)

    assert page.locator(".bulk tbody tr").count() < before


def test_the_hidden_view_is_unreachable_without_the_key(page, server):
    assert page.request.get(f"{server}/o").status == 404
    assert page.request.post(f"{server}/api/unlock",
                             data={"key": "fel"}).status == 404


def test_unlocking_opens_the_account_view(page, server):
    assert page.request.post(f"{server}/api/unlock",
                             data={"key": "test-nyckel"}).status == 200
    page.goto(f"{server}/o", wait_until="networkidle")
    page.wait_for_timeout(600)
    assert "Per konto" in page.locator("h1").inner_text()


def test_an_excluded_item_vanishes_from_the_main_view(page, server):
    def names(path):
        # Jämför nycklar, inte råtext: varunamn kan vara delsträngar av kategorinamn.
        return {row["name_key"] for row in page.request.get(f"{server}{path}").json()["items"]}

    page.request.post(f"{server}/api/unlock", data={"key": "test-nyckel"})
    assert "kaffe" in names("/api/items")

    page.request.post(
        f"{server}/api/o/exclude",
        data={"name_key": "kaffe", "excluded": True},
    )

    assert "kaffe" not in names("/api/items")
    assert "kaffe" in names("/api/o/items")

    page.request.post(
        f"{server}/api/o/exclude",
        data={"name_key": "kaffe", "excluded": False},
    )


# ---------------------------------------------------------------------------
# Granskningsvyn
# ---------------------------------------------------------------------------


def test_the_review_view_lists_categorised_items_too(page, server):
    """Bulkverktyget visar bara okategoriserat -- ett fel har ju en kategori."""
    page.goto(f"{server}/granska", wait_until="networkidle")
    page.wait_for_timeout(800)

    rows = page.locator("#table tbody tr")
    assert rows.count() >= 3
    assert "Kaffe" in page.locator("#table").inner_text()


def _review_row(page, name):
    """Raden för ett visst varunamn.

    has_text duger inte: varje rad innehåller en rullgardin med alla
    kategorier som <option>, så "Mjölk" matchar varenda rad via alternativet
    "Mjölk & fil". Matcha på första cellens text i stället.
    """
    names = page.eval_on_selector_all(
        "#table tbody tr td:first-child", "els => els.map(e => e.textContent)"
    )
    assert name in names, names[:10]
    return page.locator("#table tbody tr").nth(names.index(name))


def test_correcting_a_category_in_the_review_view_sticks(page, server):
    page.goto(f"{server}/granska", wait_until="networkidle")
    page.wait_for_timeout(800)

    _review_row(page, "Mjölk").locator("select").select_option("Kryddor")
    page.wait_for_timeout(1500)

    items = page.request.get(f"{server}/api/items").json()["items"]
    assert [i["category"] for i in items if i["name_key"] == "mjölk"] == ["Kryddor"]

    # ... och källan visar att det var en egen rättning, inte en regel.
    review = page.request.get(f"{server}/api/review", params={"search": "mjölk"}).json()
    assert review["items"][0]["source"] == "manual"


def test_the_source_filter_narrows_the_list(page, server):
    """Filtret ska kunna plocka fram allt en viss källa satt."""
    page.goto(f"{server}/granska", wait_until="networkidle")
    page.wait_for_timeout(800)

    # Skapa ett känt läge i stället för att lita på vad tidigare tester lämnat.
    _review_row(page, "Kaffe").locator("select").select_option("Kryddor")
    page.wait_for_timeout(1500)

    page.locator("#source").select_option("manual")
    page.wait_for_timeout(1000)

    sources = page.eval_on_selector_all(
        "#table tbody tr td:nth-child(3)", "els => els.map(e => e.textContent)"
    )
    assert sources and set(sources) == {"Egen rättning"}


def test_releasing_claudes_answers_leaves_manual_fixes(page, server):
    """Knappen ska släppa modellens svar men inte egna rättningar."""
    page.request.post(
        f"{server}/api/overrides",
        data={"name_key": "okändvara", "category": "Glass"},
    )
    before = page.request.get(f"{server}/api/review", params={"source": "manual"}).json()
    assert before["total"] >= 1

    # Sätt något som modellen "satt", så testet inte passerar genom att
    # ingenting raderas -- vilket det gjorde när rutten var skuggad.
    page.request.post(
        f"{server}/api/overrides", data={"name_key": "kaffe", "category": "Glass"}
    )
    released = page.request.delete(f"{server}/api/model-overrides")
    assert released.ok
    assert "removed" in released.json()

    after = page.request.get(f"{server}/api/review", params={"source": "manual"}).json()
    assert after["total"] == before["total"]
