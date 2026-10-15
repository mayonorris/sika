"""Isolated local explorer backed only by genuinely published synthetic fixtures."""

from datetime import date
import os
import sqlite3

from fastapi.testclient import TestClient
import pytest

from dev.fixture_sandbox import create_app, fixture_environment
from sika import publication, quality


FRENCH = "Quelle est l'évolution de l'inflation au Togo depuis 2023 ?"
ENGLISH = "How has inflation in Togo evolved since 2023?"
TODAY = date(2026, 10, 15)
BASE_URL = "http://127.0.0.1:8001"


def question(text=FRENCH, **fields):
    return {"version": 1, "question": text} | fields


def refused(response, status, code):
    assert response.status_code == status
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["error"]["code"] == code
    assert payload["rows"] == [] and payload["row_count"] == 0
    assert payload["has_more"] is False and payload["complete"] is False
    return payload


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    parent = tmp_path_factory.mktemp("local-fixture-tests")
    with fixture_environment(storage_parent=parent) as environment:
        before = environment.target.read_bytes()
        source_bytes = {name: path.read_bytes() for name, path in environment.sources.items()}
        yield environment
        assert environment.target.read_bytes() == before
        assert {name: path.read_bytes() for name, path in environment.sources.items()} == source_bytes


@pytest.fixture(scope="module")
def client(sandbox):
    with TestClient(create_app(sandbox), base_url=BASE_URL) as connection:
        yield connection


@pytest.mark.parametrize("text,language", [(FRENCH, "fr"), (ENGLISH, "en")])
def test_bilingual_queries_use_protected_synthetic_history(client, sandbox, monkeypatch,
                                                        text, language):
    def forbidden(*args, **kwargs):
        pytest.fail("The local fixture explorer must never call an external provider")

    monkeypatch.setattr("socket.create_connection", forbidden)
    response = client.post("/v1/query", json=question(text))
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok" and payload["language"] == language
    assert payload["row_count"] == 51
    assert payload["rows"][0]["period"] == "2023-01"
    assert payload["rows"][-1]["period"] == "2026-10"
    assert payload["complete"] is True and payload["has_more"] is False
    assert "multiple_vintages" in payload["warnings"]
    assert payload["interpretation"]["intent"]["selector"]["geography"] == "TG"
    for row in payload["rows"]:
        assert isinstance(row["value"], str)
        assert row["citation"]["source_doc"] in sandbox.sources
        assert row["citation"]["source_page"] >= 1
        assert row["citation"]["publication_event_id"]
        assert row["citation"]["content_sha256"]
        assert "synthetic" in row["citation"]["source_doc"].lower()
    assert any(row["revision_status"] == "revised" for row in payload["rows"])
    assert "p. " in payload["answer"]
    assert "snapshot_sha256" in payload and payload["as_of_utc"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_cpi_requires_base_then_explicit_base_disambiguates(client):
    refused(client.post("/v1/query", json=question("IHPC au Togo en 2026 ?")),
            422, "ambiguous_series")
    response = client.post("/v1/query", json=question("IHPC base 2023 au Togo en 2026 ?"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["row_count"] == 10
    assert payload["series"]["concept"] == "cpi_index"
    assert payload["series"]["price_basis"] == "index_2023"
    assert all(row["unit"] == "index" for row in payload["rows"])


def test_disambiguation_filter_does_not_replace_question_semantics(client):
    response = client.post("/v1/query", json=question(
        "IHPC au Togo en 2026 ?", filters={"price_basis": "index_2014"}))
    assert response.status_code == 200
    assert response.json()["series"]["price_basis"] == "index_2014"
    refused(client.post("/v1/query", json=question(
        "IHPC base 2023 au Togo en 2026 ?", filters={"price_basis": "index_2014"})),
        422, "invalid_question")


def test_truncation_and_empty_state_remain_explicit(client):
    truncated = client.post("/v1/query", json=question(limit=2))
    assert truncated.status_code == 200
    payload = truncated.json()
    assert payload["status"] == "truncated" and payload["row_count"] == 2
    assert len(payload["rows"]) == 2
    assert payload["has_more"] is True and payload["complete"] is False
    assert "result_truncated" in payload["warnings"]
    empty = client.post("/v1/query", json=question("Inflation au Togo en 2030 ?"))
    assert empty.status_code == 200
    assert empty.json()["status"] == "empty" and empty.json()["rows"] == []
    assert empty.json()["complete"] is True


@pytest.mark.parametrize("text,code", [
    ("Inflation en 2026 ?", "clarification_required"),
    ("Inflation au Togo ?", "clarification_required"),
    ("Forecast inflation in Togo in 2027", "unsupported_question"),
    ("Inflation au Togo en 2026 sauf juin", "invalid_period"),
])
def test_missing_or_unsupported_semantics_never_return_partial_data(client, text, code):
    refused(client.post("/v1/query", json=question(text)), 422, code)


@pytest.mark.parametrize("field,value", [
    ("sql", "SELECT * FROM observations"),
    ("target", "data/processed/sika.db"),
    ("source_root", "data/raw"),
    ("reviewer", "test:local-fixture-sandbox"),
    ("proof", {"approved": True}),
])
def test_browser_cannot_choose_data_or_bypass_proof(client, field, value):
    refused(client.post("/v1/query", json=question(**{field: value})), 422,
            "invalid_question")


def test_local_info_and_explorer_are_explicitly_synthetic(client, sandbox):
    response = client.get("/sandbox-info")
    assert response.status_code == 200
    payload = response.json()
    assert payload["synthetic"] is True
    assert payload["fixture_id"] == sandbox.fixture_id
    assert payload["today"] == TODAY.isoformat()
    assert payload["observation_count"] == 511 and payload["series_count"] == 11
    assert str(sandbox.root) not in response.text
    explorer = client.get("/")
    assert explorer.status_code == 200
    assert "text/html" in explorer.headers["content-type"]
    assert "SYNTHETIC" in explorer.text.upper() or "SYNTHÉTIQUE" in explorer.text.upper()
    assert "/v1/query" in explorer.text
    assert "https://" not in explorer.text and "http://" not in explorer.text


def test_synthetic_manifest_matches_physical_seed_and_publication_audit(sandbox):
    # Read only the newly owned fixture database; never import or inspect a real DB.
    with sqlite3.connect(sandbox.target.as_uri() + "?mode=ro", uri=True) as con:
        assert con.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 511
        assert con.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 11
        assert con.execute("SELECT COUNT(*) FROM releases WHERE status='published'").fetchone()[0] == 3
        assert con.execute("SELECT MIN(period), MAX(period) FROM observations").fetchone() == (
            "2023-01", "2026-10")


def test_fixture_pdf_routes_are_allowlisted_and_contain_visible_warning(client, sandbox):
    import pdfplumber

    for filename, path in sandbox.sources.items():
        response = client.get(f"/fixture-source/{filename}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/pdf")
        assert response.content == path.read_bytes()
        with pdfplumber.open(path) as pdf:
            visible = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert "SYNTHETIC" in visible.upper()
    assert client.get("/fixture-source/not-allowlisted.pdf").status_code == 404
    assert client.get("/fixture-source/sika.db").status_code == 404
    assert client.get("/fixture-source/%2e%2e%2fdata%2fprocessed%2fsika.db").status_code == 404


@pytest.mark.parametrize("path", [
    "/ask", "/brief", "/sources", "/indicators", "/docs", "/openapi.json",
    "/data/processed/sika.db", "/data/raw", "/api/main.py", "/.env",
])
def test_fixture_app_never_exposes_legacy_routes_or_repository_files(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("headers", [
    {"host": "example.org:8001"},
    {"host": "127.0.0.1:8000"},
    {"origin": "https://example.org"},
    {"origin": "http://localhost:8000"},
])
def test_loopback_browser_boundary_rejects_untrusted_origin_or_host(client, headers):
    response = client.post("/v1/query", headers=headers, json=question())
    assert response.status_code in {400, 403}
    assert "rows" not in response.json() or response.json()["rows"] == []


@pytest.mark.parametrize("origin", [BASE_URL, "http://localhost:8001"])
def test_same_port_loopback_origins_are_accepted(client, origin):
    response = client.post("/v1/query", headers={"origin": origin},
                           json=question("Inflation au Togo en juin 2026 ?"))
    assert response.status_code == 200


@pytest.mark.parametrize("change", ["source", "reviewer"])
def test_source_or_reviewer_revocation_is_checked_on_next_request(tmp_path, monkeypatch, change):
    with fixture_environment(storage_parent=tmp_path) as environment:
        with TestClient(create_app(environment), base_url=BASE_URL) as connection:
            assert connection.post("/v1/query", json=question()).status_code == 200
            if change == "reviewer":
                monkeypatch.setenv("SIKA_REVIEWER_IDS", "test:revoked-local-fixture")
            else:
                # Alter only generated fixture bytes, never either real source archive.
                source = next(path for name, path in environment.sources.items()
                              if "monthly_2023" in name)
                source.write_bytes(source.read_bytes() + b"\n% fixture tamper\n")
            result = refused(connection.post("/v1/query", json=question()), 503,
                             "publication_unavailable")
            assert str(environment.root) not in str(result)


def test_unique_roots_profiles_and_reviewer_environment_restore(tmp_path, monkeypatch):
    monkeypatch.setenv("SIKA_REVIEWER_IDS", "test:preexisting-reviewer")
    original_identity = publication.operator_identity
    original_profiles = dict(quality.PROFILES)
    with fixture_environment(storage_parent=tmp_path) as first:
        first_names = set(first.sources)
        assert os.environ["SIKA_REVIEWER_IDS"] == "test:local-fixture-sandbox"
        assert publication.operator_identity() == "test:local-fixture-sandbox"
        assert first_names.issubset(quality.PROFILES)
        with fixture_environment(storage_parent=tmp_path) as second:
            assert first.root != second.root
            assert set(first.sources).isdisjoint(second.sources)
            assert set(second.sources).issubset(quality.PROFILES)
        assert first_names.issubset(quality.PROFILES)
        assert set(second.sources).isdisjoint(quality.PROFILES)
    assert quality.PROFILES == original_profiles
    assert publication.operator_identity is original_identity
    assert os.environ["SIKA_REVIEWER_IDS"] == "test:preexisting-reviewer"


def test_environment_variables_never_select_an_existing_database(tmp_path, monkeypatch):
    unrelated = tmp_path / "existing-unrelated.db"
    marker = b"This is not the fixture database and must remain untouched."
    unrelated.write_bytes(marker)
    monkeypatch.setenv("SIKA_DB", str(unrelated))
    monkeypatch.setenv("SIKA_DATABASE_URL", f"sqlite:///{unrelated.as_posix()}")
    with fixture_environment(storage_parent=tmp_path) as environment:
        assert environment.target != unrelated
        assert environment.target.parent == environment.root
        assert environment.today == TODAY
        with TestClient(create_app(environment), base_url=BASE_URL) as connection:
            assert connection.post("/v1/query", json=question()).status_code == 200
    assert unrelated.read_bytes() == marker
