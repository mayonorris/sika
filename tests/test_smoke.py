"""Smoke tests for the local Sika demo database and no-API endpoints."""
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

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
    answer = payload["answer"].lower()
    summary_terms = ("première valeur", "dernière valeur", "minimum", "maximum")
    assert all(term in answer for term in summary_terms)
    assert f"{len(payload['rows'])} observations" in answer


def test_brief_fallback_uses_database_rows() -> None:
    response = client.post(
        "/brief", json={"topic": "inflation", "geography": "Togo"}
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["n_observations"] > 0
    assert "synthèse automatique sans analyse LLM" in payload["brief"]
    sections = (
        "# Note économique",
        "## Couverture",
        "## Chiffres clés",
        "## Lacunes des données",
    )
    assert all(section in payload["brief"] for section in sections)
    assert re.search(r"\(.*, p\. \d+\)", payload["brief"])


def test_brief_falls_back_when_llm_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAPIError(Exception):
        pass

    def fail(**_kwargs):
        raise FakeAPIError

    failing_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fail))
    )
    monkeypatch.setattr(api, "APIError", FakeAPIError)
    monkeypatch.setattr(api, "client", failing_client)

    payload = client.post("/brief", json={"topic": "inflation"}).json()

    assert payload["n_observations"] > 0
    assert "synthèse automatique sans analyse LLM" in payload["brief"]


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
    assert "height:320px" in html
    assert "height: 320" in html
    assert "answerEl.insertAdjacentElement('afterend', div)" in html
    assert "drawChart(data.rows || [], data.chart, data.title, wait)" in html
