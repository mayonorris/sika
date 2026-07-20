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
    expected_chart = "line" if len(payload["rows"]) >= 3 else "none"
    assert payload["chart"] == expected_chart
    assert payload["rows"]
    assert [row["period"] for row in payload["rows"]] == sorted(
        row["period"] for row in payload["rows"]
    )
    if len(payload["rows"]) > 2:
        answer = payload["answer"].lower()
        summary_terms = ("première valeur", "dernière valeur", "minimum", "maximum")
        assert all(term in answer for term in summary_terms)
        assert f"{len(payload['rows'])} observations" in answer


def test_plain_inflation_uses_one_headline_row_per_period() -> None:
    response = client.post(
        "/ask", json={"question": "Quelle est l'évolution de l'inflation au Togo ?"}
    )
    rows = response.json()["rows"]

    assert rows
    assert len(rows) == len({row["period"] for row in rows})
    assert api.pick_headline_rows(rows) == rows
    assert all(not api.names_different_geography(row) for row in rows)


def test_explicit_food_inflation_keeps_food_category() -> None:
    response = client.post(
        "/ask", json={"question": "Quelle est l'inflation alimentaire en 2026 ?"}
    )
    rows = response.json()["rows"]

    assert rows
    assert all("aliment" in api.normalized(row["indicator_label"]) for row in rows)
    assert len(rows) == len({row["period"] for row in rows})


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("inflation en mai 2026", ("exact", "2026-05")),
        ("inflation en février 2026", ("exact", "2026-02")),
        ("IHPC de 2026-05", ("exact", "2026-05")),
        ("inflation en 2025", ("year", "2025")),
        ("inflation since 2023", ("since", "2023")),
    ],
)
def test_parse_period(question: str, expected: tuple[str, str]) -> None:
    assert api.parse_period(question) == expected


def test_specific_month_returns_one_cited_headline_value() -> None:
    payload = client.post(
        "/ask", json={"question": "Quelle est l'inflation en mai 2026 ?"}
    ).json()

    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["period"] == "2026-05"
    assert api.pick_headline_rows(payload["rows"]) == payload["rows"]
    assert len(re.findall(r"\(.*, p\. \d+\)", payload["answer"])) == 1


def test_missing_year_does_not_substitute_another_period() -> None:
    payload = client.post(
        "/ask", json={"question": "Quelle est l'inflation en 2025 ?"}
    ).json()

    assert payload["rows"] == []
    assert payload["chart"] == "none"
    assert payload["answer"].startswith("Aucune donnée correspondante")

def test_period_deduplication_prefers_highest_confidence() -> None:
    rows = [
        {"period": "2026-05", "confidence": 0.6, "value": 1},
        {"period": "2026-05", "confidence": 0.9, "value": 2},
    ]

    assert api.dedupe_by_period(rows)[0]["value"] == 2

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


def test_extraction_confidence_is_internal_only() -> None:
    ask_payload = client.post(
        "/ask", json={"question": "Quelle est l'inflation en mai 2026 ?"}
    ).json()
    brief_payload = client.post("/brief", json={"topic": "inflation"}).json()
    forbidden = ("confidence interval", "margin of error", "95% confidence")

    assert ask_payload["rows"]
    assert all("confidence" not in row for row in ask_payload["rows"])
    assert all(term not in ask_payload["answer"].lower() for term in forbidden)
    assert all(term not in brief_payload["brief"].lower() for term in forbidden)


def test_all_llm_prompts_forbid_statistical_confidence_language() -> None:
    warning = "Never describe these figures using statistical language"

    assert all(
        warning in prompt
        for prompt in (api.ROUTER_PROMPT, api.ANSWER_PROMPT, api.BRIEF_PROMPT)
    )


def test_spot_check_labels_extraction_confidence_explicitly() -> None:
    script = Path("pipeline/spot_check.py").read_text(encoding="utf-8")

    assert "confiance d'extraction (LLM)" in script
    assert "(conf " not in script

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
    assert "économique" in html
    assert "height:320px" in html
    assert "height: 320" in html
    assert "insertAdjacentElement('afterend', div)" in html
    assert "drawChart(data.rows || [], data.chart, data.title, wait)" in html

def test_ui_uses_responsive_statistical_workspace() -> None:
    html = Path("app/index.html").read_text(encoding="utf-8")
    chat_css = re.search(r"#chat\s*\{([^}]*)\}", html, re.DOTALL)

    assert chat_css and "overflow-y" not in chat_css.group(1)
    assert "--shell:1280px" in html
    assert "app-shell sources-open" not in html
    assert ".app-shell.sources-open" in html
    assert 'class="onboarding"' in html
    assert html.count('class="suggestion"') >= 4
    assert '<textarea id="q"' in html
    assert "renderStatSummary(wait, data.rows || [], data.title)" in html
    assert "Documents officiels indexés" in html


def test_ui_supports_keyboard_drawer_and_accessibility_states() -> None:
    html = Path("app/index.html").read_text(encoding="utf-8")

    assert 'aria-controls="sourcesPanel"' in html
    assert "div.setAttribute('role', 'img')" in html
    assert "event.key === 'Enter' && !event.shiftKey" in html
    assert "briefDialog.showModal()" in html
    assert "@media (max-width:900px)" in html
    assert "@media (max-width:640px)" in html
    assert "@media (prefers-reduced-motion:reduce)" in html
    assert "overflow-x:hidden" in html
    assert "if (source.url)" in html
