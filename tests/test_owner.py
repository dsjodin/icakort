"""Ägarspårning och den dolda kontovyn."""

import json

import pytest
from fastapi.testclient import TestClient

from icakort import categorize, store, sync as sync_mod
from icakort.normalize import normalize_receipt

SECRET = "hemlig-stig-123"


@pytest.fixture
def conn(tmp_path, raw_receipt):
    conn = store.connect(tmp_path / "test.db")
    receipt = normalize_receipt(raw_receipt["receipt"], raw_receipt["list_entry"])
    store.save_receipt(conn, receipt, owner_key="konto-a", owner_name="Alex")
    categorize.recategorize(conn)
    yield conn
    conn.close()


@pytest.fixture
def client(tmp_path, monkeypatch, raw_receipt):
    monkeypatch.setenv("ICAKORT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ICAKORT_PASSWORD", raising=False)
    monkeypatch.delenv("ICAKORT_OWNER_KEY", raising=False)

    db = store.connect(tmp_path / "icakort.db")
    for key, owner_key, owner_name in (
        ("r-a", "konto-a", "Alex"),
        ("r-b", "konto-b", "Robin"),
    ):
        payload = json.loads(json.dumps(raw_receipt))
        payload["receipt"]["key"] = key
        payload["list_entry"]["key"] = key
        receipt = normalize_receipt(payload["receipt"], payload["list_entry"])
        store.save_receipt(db, receipt, owner_key=owner_key, owner_name=owner_name)
    categorize.recategorize(db)
    db.close()

    from icakort.web.app import app

    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# Datalagret
# ---------------------------------------------------------------------------


def test_migration_adds_columns_without_touching_data(tmp_path, raw_receipt):
    """En databas från före ägarspårningen ska överleva uppgraderingen."""
    import sqlite3

    path = tmp_path / "gammal.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE receipts (key TEXT PRIMARY KEY, purchase_date TEXT,
            store_name TEXT, store_id TEXT, total_ore INTEGER,
            item_sum_ore INTEGER, raw_path TEXT,
            fetched_at TEXT DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO receipts (key, purchase_date, total_ore) VALUES ('x', '2024-01-01', 5000);
        """
    )
    old.commit()
    old.close()

    conn = store.connect(path)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(receipts)")}
    assert {"owner_key", "owner_name"} <= columns

    row = conn.execute("SELECT * FROM receipts WHERE key = 'x'").fetchone()
    assert row["total_ore"] == 5000        # befintlig data orörd
    assert row["owner_key"] is None
    conn.close()


def test_reparse_does_not_erase_the_owner(tmp_path, raw_receipt, monkeypatch):
    """Utan COALESCE hade "Tolka om" nollställt vem som köpt vad, oåterkalleligt."""
    monkeypatch.setenv("ICAKORT_DATA_DIR", str(tmp_path))
    raw = tmp_path / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "rcpt-test-0001.json").write_text(
        json.dumps(raw_receipt, ensure_ascii=False), encoding="utf-8"
    )

    conn = store.connect(tmp_path / "icakort.db")
    receipt = normalize_receipt(raw_receipt["receipt"], raw_receipt["list_entry"])
    store.save_receipt(conn, receipt, owner_key="konto-a", owner_name="Alex")

    sync_mod.reparse(conn)      # kör utan token, skickar ingen ägare

    row = conn.execute("SELECT owner_key, owner_name FROM receipts").fetchone()
    assert row["owner_key"] == "konto-a"
    assert row["owner_name"] == "Alex"
    conn.close()


def test_assign_owner_never_overwrites_an_existing_one(conn, raw_receipt):
    payload = json.loads(json.dumps(raw_receipt))
    payload["receipt"]["key"] = "utan-ägare"
    payload["list_entry"]["key"] = "utan-ägare"
    store.save_receipt(conn, normalize_receipt(payload["receipt"], payload["list_entry"]))

    changed = store.assign_owner(conn, "konto-b", "Robin")

    assert changed == 1
    owners = dict(conn.execute("SELECT key, owner_name FROM receipts").fetchall())
    assert owners["rcpt-test-0001"] == "Alex"      # rörd? nej
    assert owners["utan-ägare"] == "Robin"


def test_owner_name_comes_from_the_kivra_listing(raw_receipt):
    raw_receipt["list_entry"]["accessInfo"] = {"owner": {"isMe": True, "name": "Alex A"}}
    receipt = normalize_receipt(raw_receipt["receipt"], raw_receipt["list_entry"])
    assert receipt.owner_name == "Alex A"


# ---------------------------------------------------------------------------
# Vyn är dold
# ---------------------------------------------------------------------------


def _unlock(client, key=SECRET):
    return client.post("/api/unlock", json={"key": key})


def test_hidden_by_default(client):
    """Utan nyckel i miljön ska vyn inte finnas alls."""
    assert client.get("/o").status_code == 404
    assert client.get("/api/o/summary").status_code == 404
    assert _unlock(client).status_code == 404


def test_a_wrong_key_is_indistinguishable_from_a_dead_link(client, monkeypatch):
    """Den som provar sig fram får inte kunna lära sig att vyn existerar."""
    monkeypatch.setenv("ICAKORT_OWNER_KEY", SECRET)

    missing = client.get("/finns-inte-alls")
    locked = client.get("/o")
    wrong = _unlock(client, "fel-gissning")

    assert missing.status_code == locked.status_code == wrong.status_code == 404
    assert missing.content == locked.content == wrong.content


def test_unlocking_sets_a_cookie_and_opens_the_view(client, monkeypatch):
    monkeypatch.setenv("ICAKORT_OWNER_KEY", SECRET)

    response = _unlock(client)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "strict" in cookie.lower()

    page = client.get("/o")
    assert page.status_code == 200
    assert "Per konto" in page.text

    data = client.get("/api/o/summary").json()
    assert {row["owner"] for row in data["by_owner"]} == {"Alex", "Robin"}
    assert data["household_ore"] == 17943 * 2


def test_the_key_never_appears_in_a_url(client, monkeypatch):
    """Nyckeln i sökvägen skulle hamna i historiken -- den vägen ska vara borta."""
    monkeypatch.setenv("ICAKORT_OWNER_KEY", SECRET)
    _unlock(client)

    assert client.get(f"/o/{SECRET}").status_code == 404
    assert client.get(f"/api/o/{SECRET}/summary").status_code == 404


def test_items_can_be_filtered_per_owner(client, monkeypatch):
    monkeypatch.setenv("ICAKORT_OWNER_KEY", SECRET)
    _unlock(client)
    data = client.get("/api/o/items", params={"owner": "konto-a"}).json()
    assert data["items"]
    assert all(row["total_ore"] <= 17943 for row in data["items"])


def test_the_ordinary_dashboard_never_mentions_owners(client, monkeypatch):
    """Huvudvyn får inte läcka attributionen, ens när nyckeln är satt."""
    monkeypatch.setenv("ICAKORT_OWNER_KEY", SECRET)
    _unlock(client)

    for path in ("/api/filters", "/api/overview", "/api/items", "/api/quality"):
        body = client.get(path).text
        assert "owner" not in body.lower(), path

    # Sidan får inte länka till den dolda vyn. ("/o" ensamt duger inte som
    # test -- det matchar </option> i markupen.)
    body = client.get("/").text
    assert 'href="/o"' not in body
    assert "/api/o/" not in body


def test_the_hidden_view_still_needs_the_password(client, monkeypatch):
    """Nyckeln är en extra spärr innanför lösenordet, inte en väg förbi."""
    monkeypatch.setenv("ICAKORT_OWNER_KEY", SECRET)
    monkeypatch.setenv("ICAKORT_PASSWORD", "hemligt")

    assert client.post("/api/unlock", json={"key": SECRET}).status_code == 401

    client.post("/api/unlock", json={"key": SECRET}, auth=("icakort", "hemligt"))
    assert client.get("/o").status_code == 401
    assert client.get("/o", auth=("icakort", "hemligt")).status_code == 200


# ---------------------------------------------------------------------------
# Undantagna varor
# ---------------------------------------------------------------------------


def test_an_excluded_item_disappears_from_every_ordinary_view(client, monkeypatch):
    """En dold present får inte synas någonstans i huvudvyn -- inte heller
    som ett namn i kvalitetslistan eller som en punkt i priskurvan."""
    monkeypatch.setenv("ICAKORT_OWNER_KEY", SECRET)
    _unlock(client)

    before = client.get("/api/overview").json()["summary"]["total_ore"]
    assert "PRYLBURK XYZ" in client.get("/api/items").text

    assert client.post(
        "/api/o/exclude", json={"name_key": "prylburk xyz", "excluded": True}
    ).status_code == 200

    assert "PRYLBURK XYZ" not in client.get("/api/items").text
    assert "PRYLBURK XYZ" not in client.get("/api/quality").text
    assert client.get("/api/overview").json()["summary"]["total_ore"] < before
    assert client.get("/api/price", params={"name_key": "prylburk xyz"}).json()["points"] == []

    # ... men den finns kvar bakom nyckeln.
    hidden = client.get("/api/o/summary").json()
    assert [row["name_key"] for row in hidden["excluded"]] == ["prylburk xyz"]


def test_excluding_survives_a_reparse(client, monkeypatch, tmp_path, raw_receipt):
    """En omtolkning bygger om items -- undantaget får inte tvättas bort."""
    import json

    monkeypatch.setenv("ICAKORT_OWNER_KEY", SECRET)
    _unlock(client)
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    payload = json.loads(json.dumps(raw_receipt))
    payload["receipt"]["key"] = "r-a"
    payload["list_entry"]["key"] = "r-a"
    (raw / "r-a.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    client.post("/api/o/exclude", json={"name_key": "prylburk xyz", "excluded": True})
    sync_mod.reparse(store.connect(tmp_path / "icakort.db"))

    assert "PRYLBURK XYZ" not in client.get("/api/items").text


def test_exclusion_can_be_undone(client, monkeypatch):
    monkeypatch.setenv("ICAKORT_OWNER_KEY", SECRET)
    _unlock(client)
    client.post("/api/o/exclude", json={"name_key": "prylburk xyz", "excluded": True})
    client.post("/api/o/exclude", json={"name_key": "prylburk xyz", "excluded": False})
    assert "PRYLBURK XYZ" in client.get("/api/items").text
