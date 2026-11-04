"""S0.6 liveness/readiness: dependency failures are visible before users hit them."""
import hashlib
import json
import logging
import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

import api.main as api
from sika import health
from sika.observability import LOGGER_NAME, JsonFormatter

ROOT = Path(__file__).resolve().parents[1]
COMMITTED_DB = ROOT / "data" / "processed" / "sika.db"
client = TestClient(api.app)


@pytest.fixture
def events():
    captured: list[dict] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(json.loads(JsonFormatter().format(record)))

    handler = Capture()
    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(handler)
    yield captured
    logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def no_trust_core(monkeypatch):
    monkeypatch.delenv("SIKA_DATABASE_URL", raising=False)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def legacy_db(path: Path, *, rows: int = 1, drop_column: str | None = None,
              passages: bool = True) -> Path:
    columns = ["indicator", "indicator_label", "geography", "period", "value", "unit",
               "source_doc", "source_page", "confidence"]
    if drop_column:
        columns.remove(drop_column)
    with sqlite3.connect(path) as con:
        con.execute(f"CREATE TABLE observations (id INTEGER PRIMARY KEY, {', '.join(columns)})")
        if passages:
            con.execute("CREATE TABLE passages (id INTEGER PRIMARY KEY, source_doc, page, text)")
        for _ in range(rows):
            con.execute(f"INSERT INTO observations ({columns[0]}) VALUES ('x')")
    return path


def trust_core(path: Path, revision: str = "head") -> str:
    url = str(sa.URL.create("sqlite", database=str(path)))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, revision)
    return url


def ready(monkeypatch, legacy: Path, url: str | None = None):
    monkeypatch.setattr(api, "DB_PATH", str(legacy))
    if url is not None:
        monkeypatch.setenv("SIKA_DATABASE_URL", url)
    return client.get("/health/ready")


def failed(response, check: str, reason: str) -> dict:
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"][check]["status"] == "fail"
    assert body["checks"][check]["reason"] == reason
    return body


def test_liveness_checks_no_dependency(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "absent.db"))
    response = client.get("/health/live")
    assert response.status_code == 200 and response.json() == {"status": "alive"}
    assert not (tmp_path / "absent.db").exists()


def test_committed_database_is_ready_without_trust_core():
    before = sha256(COMMITTED_DB)
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["legacy_database"]["status"] == "pass"
    assert body["checks"]["legacy_database"]["observations"] >= 224
    assert body["checks"]["trust_core_schema"] == {"status": "skipped", "reason": "not_configured"}
    assert sha256(COMMITTED_DB) == before


def test_health_routes_are_not_shadowed_by_static_mount():
    assert client.get("/health/ready").headers["content-type"].startswith("application/json")


def test_missing_database_fails_readiness_without_creating_it(monkeypatch, tmp_path, events):
    missing = tmp_path / "missing.db"
    body = failed(ready(monkeypatch, missing), "legacy_database", "database_missing")
    assert not missing.exists()
    assert str(tmp_path) not in json.dumps(body)
    alert = [event for event in events if event["event"] == "health.not_ready"]
    assert alert and alert[0]["level"] == "error"
    assert alert[0]["checks"]["legacy_database"]["reason"] == "database_missing"


def test_database_without_tables_fails_readiness(monkeypatch, tmp_path):
    empty_file = tmp_path / "blank.db"
    sqlite3.connect(empty_file).close()
    body = failed(ready(monkeypatch, empty_file), "legacy_database", "schema_missing")
    assert body["checks"]["legacy_database"]["table"] == "observations"


def test_missing_passages_table_fails_readiness(monkeypatch, tmp_path):
    path = legacy_db(tmp_path / "no-passages.db", passages=False)
    body = failed(ready(monkeypatch, path), "legacy_database", "schema_missing")
    assert body["checks"]["legacy_database"]["table"] == "passages"


def test_missing_provenance_column_fails_readiness(monkeypatch, tmp_path):
    path = legacy_db(tmp_path / "no-page.db", drop_column="source_page")
    body = failed(ready(monkeypatch, path), "legacy_database", "schema_incomplete")
    assert body["checks"]["legacy_database"]["missing_columns"] == ["source_page"]


def test_empty_observations_fail_readiness(monkeypatch, tmp_path):
    failed(ready(monkeypatch, legacy_db(tmp_path / "empty.db", rows=0)),
           "legacy_database", "database_empty")


def test_corrupt_database_fails_readiness(monkeypatch, tmp_path):
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not a sqlite database" * 100)
    failed(ready(monkeypatch, corrupt), "legacy_database", "database_unreadable")


def test_readiness_never_writes_the_checked_database(monkeypatch, tmp_path):
    path = legacy_db(tmp_path / "served.db")
    before = sha256(path), path.stat().st_mtime_ns
    assert ready(monkeypatch, path).status_code == 200
    assert (sha256(path), path.stat().st_mtime_ns) == before
    assert sorted(item.name for item in tmp_path.iterdir()) == ["served.db"]


def test_trust_core_at_head_is_ready(monkeypatch, tmp_path):
    url = trust_core(tmp_path / "v2.db")
    before = sha256(tmp_path / "v2.db")
    response = ready(monkeypatch, COMMITTED_DB, url)
    assert response.status_code == 200
    check = response.json()["checks"]["trust_core_schema"]
    assert check["status"] == "pass" and check["current"] == check["head"]
    assert check["head"] == list(health.migration_heads())
    assert sha256(tmp_path / "v2.db") == before


def test_pending_migration_fails_readiness(monkeypatch, tmp_path, events):
    url = trust_core(tmp_path / "behind.db", "0002_inflation_audit")
    body = failed(ready(monkeypatch, COMMITTED_DB, url), "trust_core_schema", "migration_pending")
    check = body["checks"]["trust_core_schema"]
    assert check["current"] == ["0002_inflation_audit"]
    assert check["head"] == list(health.migration_heads()) != check["current"]
    assert body["checks"]["legacy_database"]["status"] == "pass"
    assert any(event["event"] == "health.not_ready" for event in events)


def test_configured_trust_core_must_exist_and_is_not_created(monkeypatch, tmp_path):
    missing = tmp_path / "v2-missing.db"
    url = str(sa.URL.create("sqlite", database=str(missing)))
    failed(ready(monkeypatch, COMMITTED_DB, url), "trust_core_schema", "database_missing")
    assert not missing.exists()


def test_unversioned_trust_core_fails_readiness(monkeypatch, tmp_path):
    path = tmp_path / "unversioned.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE stray (id INTEGER)")
    url = str(sa.URL.create("sqlite", database=str(path)))
    failed(ready(monkeypatch, COMMITTED_DB, url), "trust_core_schema", "unversioned")


def test_database_newer_than_code_fails_readiness(monkeypatch, tmp_path):
    path = tmp_path / "future.db"
    url = trust_core(path)
    with sqlite3.connect(path) as con:
        con.execute("UPDATE alembic_version SET version_num = '9999_from_the_future'")
    failed(ready(monkeypatch, COMMITTED_DB, url), "trust_core_schema", "unknown_revision")


def test_unreachable_postgres_is_reported_without_leaking_the_url(monkeypatch):
    url = "postgresql://operator:s3cret-password@db.invalid:5432/sika"
    response = ready(monkeypatch, COMMITTED_DB, url)
    assert response.status_code == 503
    check = response.json()["checks"]["trust_core_schema"]
    # No PostgreSQL driver is a declared dependency; with one, the host is unreachable.
    assert check["status"] == "fail"
    assert check["reason"] in {"driver_unavailable", "database_unreachable"}
    assert check["backend"] == "postgresql"
    for secret in ("s3cret", "db.invalid", "operator"):
        assert secret not in response.text


@pytest.mark.parametrize("url,reason", [
    ("mysql://user:pw@host/db", "unsupported_backend"),
    ("sqlite://", "invalid_url"),
    ("::not a url::", "invalid_url"),
])
def test_unsupported_trust_core_urls_fail_readiness(monkeypatch, url, reason):
    failed(ready(monkeypatch, COMMITTED_DB, url), "trust_core_schema", reason)


def test_successful_probes_are_not_request_logged(events):
    client.get("/health/live")
    client.get("/health/ready")
    assert not [event for event in events if event["event"] == "http.request"]
