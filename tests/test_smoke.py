"""Smoke tests for the local Sika demo database and no-API endpoints."""
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.main as api

client = TestClient(api.app)


@pytest.fixture(autouse=True)
def disable_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "client", None)


def test_database_has_expected_corpus() -> None:
    with sqlite3.connect(api.DB_PATH) as con:
        total, real = con.execute(
            """SELECT COUNT(*),
                      SUM(CASE WHEN source_doc NOT LIKE 'FIXTURE%' THEN 1 ELSE 0 END)
               FROM observations"""
        ).fetchone()
    assert total >= 224
    assert real >= 224


def test_indicators_returns_catalog() -> None:
    response = client.get("/indicators")
    assert response.status_code == 200
    assert any(item["indicator"] == "industrial_production_index" for item in response.json())


def test_ask_gq1_returns_cited_series() -> None:
    response = client.post(
        "/ask",
        json={"question": "Quelle est l'évolution de l'inflation au Togo depuis 2023 ?"},
    )
    payload = response.json()
    assert response.status_code == 200
    assert re.search(r"\(.*, p\. \d+\)", payload["answer"])
    assert payload["chart"] == "line"
    assert len(payload["rows"]) >= 3
    assert [row["period"] for row in payload["rows"]] == sorted(
        row["period"] for row in payload["rows"]
    )


def test_sources_lists_only_real_ingested_documents() -> None:
    response = client.get("/sources")
    sources = response.json()
    assert response.status_code == 200
    assert len(sources) >= 3
    assert sum(source["observations"] for source in sources) >= 224
    assert all(not source["source_doc"].startswith("FIXTURE") for source in sources)
    assert all(source["publisher"] and source["title"] and source["pages"] for source in sources)


def test_ui_is_valid_utf8_without_mojibake() -> None:
    html = Path("app/index.html").read_text(encoding="utf-8")
    assert all(marker not in html for marker in ("Ã", "Â", "â€", "�"))
    assert "économie" in html
