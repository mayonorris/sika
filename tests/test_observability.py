"""S0.6 structured logs: requests, per-query records and ingestion/validation jobs."""
import json
import logging
import sqlite3
import sys
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import APITimeoutError

import api.main as api
import pipeline.extract as extract
import pipeline.extract_bceao as extract_bceao
import pipeline.extract_xlsx as extract_xlsx
from api.query import QueryFixtureSettings, build_query_router
from pipeline import validate
from sika.observability import (
    LOGGER_NAME, JsonFormatter, RequestLogMiddleware, configure_logging, job_run,
)
# Fixtures for a genuinely QA'd/approved/published synthetic history (as test_query_api).
from test_publication import authorized, ready  # noqa: F401
from test_quality import store  # noqa: F401
from test_query_execution import published_history  # noqa: F401

QUESTION = "Quelle est l'évolution de l'inflation au Togo depuis 2023 ?"
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
def no_provider(monkeypatch):
    monkeypatch.setattr(api, "client", None)


def named(events: list[dict], name: str) -> list[dict]:
    return [event for event in events if event["event"] == name]


def stub_client(*outcomes) -> SimpleNamespace:
    queue = list(outcomes)

    def create(**_kwargs):
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))],
                               usage=SimpleNamespace(total_tokens=10))

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


# --- JSON format and request logs -------------------------------------------------

def test_events_are_single_line_json_with_utc_timestamp(capsys):
    configure_logging()
    with job_run("test.format", note="ligne\nunique"):
        pass
    lines = [line for line in capsys.readouterr().err.splitlines() if line]
    assert len(lines) == 2
    for line in lines:
        event = json.loads(line)
        assert event["ts"].endswith("Z") and event["level"] == "info"
    assert json.loads(lines[0])["note"] == "ligne\nunique"


def test_request_log_carries_id_status_duration_and_no_query_string(events):
    response = client.get("/indicators?token=should-not-be-logged")
    assert response.status_code == 200
    (event,) = named(events, "http.request")
    assert event["request_id"] == response.headers["x-request-id"]
    assert event["method"] == "GET" and event["path"] == "/indicators"
    assert event["status"] == 200 and event["outcome"] == "completed"
    assert isinstance(event["duration_ms"], float)
    assert "should-not-be-logged" not in json.dumps(events)


def test_wellformed_inbound_request_id_is_reused_and_bad_one_replaced(events):
    good = client.get("/sources", headers={"X-Request-ID": "upstream-abc-123"})
    bad = client.get("/sources", headers={"X-Request-ID": "x\" injected"})
    assert good.headers["x-request-id"] == "upstream-abc-123"
    assert bad.headers["x-request-id"] != "x\" injected"
    assert [event["request_id"] for event in named(events, "http.request")] == [
        "upstream-abc-123", bad.headers["x-request-id"]]


def test_unhandled_error_is_logged_as_a_500_request(events):
    app = FastAPI()

    @app.get("/boom")
    def boom():
        raise RuntimeError("private detail")

    app.add_middleware(RequestLogMiddleware)
    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    (event,) = named(events, "http.request")
    assert event["level"] == "error" and event["status"] == 500
    assert event["outcome"] == "error" and "private detail" not in json.dumps(events)


def test_failed_readiness_probe_is_request_logged(events, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "absent.db"))
    assert client.get("/health/ready").status_code == 503
    (event,) = named(events, "http.request")
    assert event["path"] == "/health/ready" and event["status"] == 503


# --- Per-query records ------------------------------------------------------------

def test_deterministic_ask_records_latency_count_and_fallback_reason(events):
    response = client.post("/ask", json={"question": QUESTION})
    rows = response.json()["rows"]
    assert rows
    (record,) = named(events, "query.completed")
    assert record["request_id"] == response.headers["x-request-id"]
    assert record["route"] == "/ask" and record["mode"] == "deterministic"
    assert record["result_count"] == len(rows)
    assert record["fallback_reason"] == "provider_not_configured"
    assert record["validation_status"] == "not_applicable"
    assert record["outcome"] == "answered" and record["provider_calls"] == 0
    assert isinstance(record["latency_ms"], float) and record["latency_ms"] >= 0
    assert record["question_chars"] == len(QUESTION)
    assert QUESTION not in json.dumps(events) and "inflation" not in json.dumps(events)


def test_unanswerable_ask_records_no_data(events):
    client.post("/ask", json={"question": "Quel temps fait-il demain ?"})
    (record,) = named(events, "query.completed")
    assert record["outcome"] == "no_data" and record["result_count"] == 0


def test_unavailable_database_is_recorded_as_fallback_reason(events, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "DB_PATH", str(tmp_path / "gone.db"))
    response = client.post("/ask", json={"question": QUESTION})
    assert response.status_code == 200 and "indisponible" in response.json()["answer"]
    (record,) = named(events, "query.completed")
    assert record["fallback_reason"] == "database_unavailable"
    assert record["outcome"] == "database_unavailable" and record["result_count"] == 0
    assert record["level"] == "error"


@pytest.mark.parametrize("sql,status", [
    ("SELECT indicator, indicator_label, geography, period, value, unit, source_doc, "
     "source_page, confidence FROM observations WHERE indicator = 'inflation_rate_yoy' "
     "AND geography = 'Togo' ORDER BY period LIMIT 200", "accepted"),
    ("DELETE FROM observations", "rejected"),
    ("SELECT * FROM missing_table", "execution_error"),
    (None, "no_sql"),
])
def test_llm_ask_records_validation_status_and_provider_usage(events, monkeypatch, sql, status):
    route = json.dumps({"sql": sql, "chart": "none", "title": ""})
    monkeypatch.setattr(api, "client", stub_client(route, "Réponse (doc.pdf, p. 1)."))
    response = client.post("/ask", json={"question": QUESTION})
    assert response.status_code == 200
    (record,) = named(events, "query.completed")
    assert record["mode"] == "llm" and record["fallback_reason"] is None
    assert record["validation_status"] == status
    assert record["result_count"] == len(response.json()["rows"])
    assert (record["result_count"] > 0) == (status == "accepted")
    assert record["provider_calls"] == 2 and record["provider_tokens"] == 20


def test_provider_failure_is_recorded_and_still_returns_dignified_503(events, monkeypatch):
    timeout = APITimeoutError(request=httpx.Request("POST", "https://provider.invalid"))
    monkeypatch.setattr(api, "client", stub_client(timeout))
    response = client.post("/ask", json={"question": QUESTION})
    assert response.status_code == 503 and "indisponible" in response.json()["detail"]
    (record,) = named(events, "query.completed")
    assert record["outcome"] == "provider_error" and record["level"] == "error"


def test_brief_records_deterministic_and_provider_fallbacks(events, monkeypatch):
    payload = client.post("/brief", json={"topic": "inflation"}).json()
    timeout = APITimeoutError(request=httpx.Request("POST", "https://provider.invalid"))
    monkeypatch.setattr(api, "client", stub_client(timeout))
    client.post("/brief", json={"topic": "inflation"})
    first, second = named(events, "query.completed")
    assert first["route"] == "/brief" and first["fallback_reason"] == "provider_not_configured"
    assert first["result_count"] == payload["n_observations"] > 0
    assert second["fallback_reason"] == "provider_error" and second["mode"] == "deterministic"


def test_disabled_protected_query_records_refusal(events):
    app = FastAPI()
    app.include_router(build_query_router())
    response = TestClient(app).post("/v1/query", json={"version": 1, "question": QUESTION})
    assert response.status_code == 503
    (record,) = named(events, "query.completed")
    assert record["route"] == "/v1/query" and record["validation_status"] == "query_disabled"
    assert record["outcome"] == "unavailable" and record["status"] == 503
    assert record["level"] == "error"
    assert record["request_id"] == response.json()["request_id"]


def test_protected_query_records_accepted_result(events, published_history):  # noqa: F811
    fixture, _event = published_history
    target = fixture[0]
    from datetime import date
    app = FastAPI()
    app.include_router(build_query_router(QueryFixtureSettings(
        fixture_root=target.parent, target=target, today=date(2026, 10, 15))))
    with TestClient(app) as http:
        response = http.post("/v1/query", json={"version": 1, "question": QUESTION})
    assert response.status_code == 200
    (record,) = named(events, "query.completed")
    assert record["validation_status"] == "accepted" and record["outcome"] == "answered"
    assert record["result_count"] == response.json()["row_count"] == 3
    assert record["has_more"] is False and record["fallback_reason"] is None
    assert record["level"] == "info"


def test_main_app_query_route_shares_the_request_id(events):
    response = client.post("/v1/query", json={"version": 1, "question": QUESTION})
    assert response.status_code == 503
    assert response.json()["request_id"] == response.headers["x-request-id"]
    (record,) = named(events, "query.completed")
    assert record["request_id"] == response.headers["x-request-id"]


# --- Ingestion and validation jobs ------------------------------------------------

def test_job_success_and_failure_events_share_one_job_id(events):
    with job_run("test.ok", target="a") as job:
        job.update(observations=3)
    with pytest.raises(ValueError):
        with job_run("test.boom"):
            raise ValueError("private source text")
    started, succeeded, started_2, failed = events
    assert started["event"] == "job.started" and succeeded["event"] == "job.succeeded"
    assert started["job_id"] == succeeded["job_id"] != started_2["job_id"] == failed["job_id"]
    assert succeeded["observations"] == 3 and succeeded["status"] == "succeeded"
    assert failed["event"] == "job.failed" and failed["level"] == "error"
    assert failed["reason"] == "exception" and failed["error_type"] == "ValueError"
    assert "private source text" not in json.dumps(events)


def test_clean_system_exit_is_success_and_error_exit_is_failure(events):
    with pytest.raises(SystemExit):
        with job_run("test.exit0"):
            raise SystemExit(0)
    with pytest.raises(SystemExit):
        with job_run("test.exit1"):
            raise SystemExit("No files")
    assert [event["event"] for event in named(events, "job.succeeded")] == ["job.succeeded"]
    (failure,) = named(events, "job.failed")
    assert failure["kind"] == "test.exit1" and failure["reason"] == "exit"


def validation_db(path, *, invalid: bool):
    with sqlite3.connect(path) as con:
        con.execute("""CREATE TABLE observations (id INTEGER PRIMARY KEY, indicator, indicator_label,
                       geography, period, value, unit, source_doc, source_page, confidence)""")
        for index, period in enumerate(["2024", "2025-Q1", "2025-01", "2025-02", "2025-03"], 1):
            con.execute("INSERT INTO observations VALUES (NULL, 'cpi_index', 'IHPC', 'Togo', ?, ?, ?, "
                        "'source.pdf', ?, 1.0)",
                        ("2025-13" if invalid and index == 1 else period, float(index), "index", index))
    return path


def test_failed_validation_run_is_a_visible_job_failure(events, tmp_path, capsys):
    assert validate.main([str(validation_db(tmp_path / "bad.db", invalid=True))]) == 1
    (failure,) = named(events, "job.failed")
    assert failure["kind"] == "validation.legacy" and failure["reason"] == "hard_failures"
    assert failure["hard_failures"] >= 1 and failure["result"] == "FAIL"
    assert "Result: FAIL" in capsys.readouterr().out


def test_passing_validation_run_is_a_job_success(events, tmp_path):
    assert validate.main([str(validation_db(tmp_path / "good.db", invalid=False))]) == 0
    (success,) = named(events, "job.succeeded")
    assert success["hard_failures"] == 0 and success["observations"] == 5


def test_validation_of_missing_database_is_a_job_failure(events, tmp_path):
    assert validate.main([str(tmp_path / "absent.db")]) == 1
    (failure,) = named(events, "job.failed")
    assert failure["reason"] == "database_missing"
    assert not (tmp_path / "absent.db").exists()


def test_pdf_ingestion_failure_reports_progress_before_the_error(events, tmp_path, monkeypatch):
    for name in ("a.pdf", "b.pdf"):
        (tmp_path / name).write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(extract, "DB_PATH", str(tmp_path / "out.db"))
    outcomes = iter([4, RuntimeError("provider quota text")])

    def process_pdf(*_args, **_kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(extract, "process_pdf", process_pdf)
    with pytest.raises(RuntimeError):
        extract.main([str(tmp_path)])
    (step,) = named(events, "job.document.processed")
    assert step["source_doc"] == "a.pdf" and step["observations"] == 4
    (failure,) = named(events, "job.failed")
    assert failure["kind"] == "ingestion.pdf" and failure["error_type"] == "RuntimeError"
    assert failure["documents"] == 2 and failure["documents_completed"] == 1
    assert failure["observations"] == 4
    assert "provider quota text" not in json.dumps(events)


def test_pdf_ingestion_success_reports_totals(events, tmp_path, monkeypatch):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(extract, "DB_PATH", str(tmp_path / "out.db"))
    monkeypatch.setattr(extract, "process_pdf", lambda *_a, **_k: 7)
    extract.main([str(tmp_path / "a.pdf")])
    (success,) = named(events, "job.succeeded")
    assert success["documents_completed"] == 1 and success["observations"] == 7


def test_xlsx_ingestion_without_input_is_a_job_failure(events, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["extract_xlsx.py", str(tmp_path)])
    with pytest.raises(SystemExit):
        extract_xlsx.main()
    (failure,) = named(events, "job.failed")
    assert failure["kind"] == "ingestion.xlsx" and failure["reason"] == "exit"


def test_bceao_ingestion_with_unavailable_database_is_a_job_failure(events, tmp_path, monkeypatch):
    monkeypatch.setattr(extract_bceao, "DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.setattr(sys, "argv", ["extract_bceao.py", str(tmp_path)])
    with pytest.raises(SystemExit):
        extract_bceao.main()
    (failure,) = named(events, "job.failed")
    assert failure["kind"] == "ingestion.bceao" and failure["reason"] == "database_unavailable"
