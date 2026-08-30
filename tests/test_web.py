"""Webbappen: lösenordsskyddet, kategoriväljaren och att jobben går att starta."""

import json

import pytest
from fastapi.testclient import TestClient

from icakort import categorize, config, store
from icakort.normalize import normalize_receipt


@pytest.fixture
def client(tmp_path, monkeypatch, raw_receipt):
    monkeypatch.setenv("ICAKORT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ICAKORT_PASSWORD", raising=False)

    conn = store.connect(tmp_path / "icakort.db")
    store.save_receipt(conn, normalize_receipt(raw_receipt["receipt"], raw_receipt["list_entry"]))
    categorize.recategorize(conn)
    conn.close()

    from icakort.web.app import app

    with TestClient(app) as client:
        yield client


def test_healthz_needs_no_password(client, monkeypatch):
    monkeypatch.setenv("ICAKORT_PASSWORD", "hemligt")
    assert client.get("/healthz").status_code == 200


def test_dashboard_is_protected_when_a_password_is_set(client, monkeypatch):
    monkeypatch.setenv("ICAKORT_PASSWORD", "hemligt")

    unauthorized = client.get("/")
    assert unauthorized.status_code == 401
    assert "Basic" in unauthorized.headers["WWW-Authenticate"]

    assert client.get("/", auth=("icakort", "hemligt")).status_code == 200
    assert client.get("/", auth=("icakort", "fel")).status_code == 401
    assert client.get("/", auth=("någon", "hemligt")).status_code == 401


def test_no_password_means_no_prompt(client):
    assert client.get("/").status_code == 200


def test_session_reports_logged_out(client):
    assert client.get("/api/session").json()["authenticated"] is False


def test_sync_without_login_is_rejected(client):
    response = client.post("/api/job/sync", json={})
    assert response.status_code == 409
    assert "Logga in" in response.json()["detail"]


def test_qr_is_absent_when_no_job_runs(client):
    assert client.get("/api/job/qr.svg").status_code == 404
    assert client.get("/api/job").json()["state"] == "idle"


def test_filters_offer_every_category_not_just_the_used_ones(client):
    data = client.get("/api/filters").json()
    # Kategoriväljaren måste kunna erbjuda kategorier som inte förekommer än.
    assert "Fisk & skaldjur" in data["all_categories"]
    assert "Fisk & skaldjur" not in data["categories"]
    # Gruppnivån ska följa med, annars kan diagrammet inte rulla upp.
    assert "Mejeri & ägg" in data["all_groups"]


def test_setting_a_category_from_the_ui_sticks(client):
    before = client.get("/api/quality").json()
    assert before["coverage"]["unknown_ore"] == 2500

    response = client.post(
        "/api/overrides", json={"name_key": "PRYLBURK XYZ", "category": "Städ & rengöring"}
    )
    assert response.status_code == 200
    assert response.json()["name_key"] == "prylburk xyz"   # normaliseras

    after = client.get("/api/quality").json()
    assert after["coverage"]["unknown_ore"] == 0

    items = client.get("/api/items").json()["items"]
    assert [i["category"] for i in items if i["name"] == "PRYLBURK XYZ"] == ["Städ & rengöring"]


def test_removing_an_override_restores_the_rules(client):
    client.post("/api/overrides", json={"name_key": "prylburk xyz", "category": "Pasta & ris"})
    client.delete("/api/overrides/prylburk xyz")
    assert client.get("/api/quality").json()["coverage"]["unknown_ore"] == 2500


def test_categories_file_is_seeded_into_the_data_dir(client, tmp_path):
    """Regelfilen ska dyka upp i volymen så den går att redigera på värden."""
    seeded = tmp_path / "categories.yaml"
    assert seeded.exists()
    assert seeded.read_text(encoding="utf-8") == config.DEFAULT_CATEGORIES.read_text(
        encoding="utf-8"
    )


def test_an_edited_categories_file_of_the_current_version_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("ICAKORT_DATA_DIR", str(tmp_path))
    path = tmp_path / "categories.yaml"
    path.write_text("version: 2\nfallback: Eget\ncategories: []\n", encoding="utf-8")

    config.ensure_categories_file()
    assert path.read_text(encoding="utf-8") == "version: 2\nfallback: Eget\ncategories: []\n"
    assert categorize.load_ruleset().fallback == "Eget"


def test_an_older_categories_file_is_upgraded_but_kept(tmp_path, monkeypatch):
    """Nya taxonomin måste nå en befintlig installation -- utan att kasta bort
    det som redan stod i filen."""
    monkeypatch.setenv("ICAKORT_DATA_DIR", str(tmp_path))
    path = tmp_path / "categories.yaml"
    path.write_text("fallback: Eget\ncategories: []\n", encoding="utf-8")

    config.ensure_categories_file()

    assert categorize.load_ruleset().fallback == "Okategoriserat"      # ny taxonomi
    assert (tmp_path / "categories.v1.bak").read_text(encoding="utf-8") == (
        "fallback: Eget\ncategories: []\n"                            # gamla kvar
    )
