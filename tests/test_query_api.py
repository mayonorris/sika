"""Fixture-only HTTP integration for the proof-bound natural-language pilot.

The synthetic publications use the real QA/review/publication workflow. Timing
tests replace execution only to isolate transport scheduling, never to certify a
source. No test calls a model provider or opens either real Sika database.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import date
import json
from pathlib import Path
import sqlite3
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest
import sqlalchemy as sa

from api import query as query_api
from api.query import QueryFixtureSettings, build_query_router
from sika import publication, quality
from sika.query import QueryError
from sika.query_execution import ExecutionLimits, execute_query
from test_publication import authorized, ready, state
from test_quality import DOC, pdf_bytes, store
from test_query_execution import (
    add_history, add_vintage, intent, publish_synthetic, published_history,
    published_vintages,
)


FRENCH = "Quelle est l'évolution de l'inflation au Togo depuis 2023 ?"
ENGLISH = "How has inflation in Togo evolved since 2023?"
TODAY = date(2026, 10, 15)


def request(question=FRENCH, **changes):
    return {"version": 1, "question": question} | changes


def settings(target, **changes):
    return QueryFixtureSettings(fixture_root=target.parent, target=target, today=TODAY,
                                **changes)


def app_for(configuration=None):
    app = FastAPI()
    app.include_router(build_query_router(configuration))
    return app


def error(response, status, code=None):
    assert response.status_code == status
    value = response.json()
    assert value["version"] == 1 and value["status"] == "error"
    assert value["rows"] == [] and value["row_count"] == 0
    assert value["has_more"] is False and value["complete"] is False
    assert value["error"]["message"]
    if code is not None:
        assert value["error"]["code"] == code
    assert value["language"] in {"fr", "en"}
    assert value["request_id"]
    assert "series" not in value and "candidate_keys" not in value["error"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    return value


@pytest.fixture
def transport_app(tmp_path, monkeypatch):
    """No publication is fabricated: execution must not be reached in these tests."""
    target = tmp_path / "unqueried.db"
    with sqlite3.connect(target) as con:
        con.execute("CREATE TABLE untouched(value INTEGER)")

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid HTTP input must fail before protected execution")

    monkeypatch.setattr(query_api, "execute_query", forbidden)
    return app_for(settings(target))


@pytest.mark.parametrize("question,language", [(FRENCH, "fr"), (ENGLISH, "en")])
def test_golden_question_http_returns_exact_cited_fixture_history(
        published_history, question, language, monkeypatch):
    fixture, event = published_history
    target, _, _, artifact = fixture
    before = target.read_bytes(), artifact.read_bytes(), state(fixture)

    def forbidden(*args, **kwargs):
        pytest.fail("The deterministic query pilot must not make provider calls")

    monkeypatch.setattr("socket.create_connection", forbidden)
    with TestClient(app_for(settings(target))) as client:
        response = client.post("/v1/query", json=request(question))
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ok" and result["language"] == language
    assert result["row_count"] == 3 and result["complete"] is True
    assert [row["period"] for row in result["rows"]] == ["2023-01", "2024-02", "2026-05"]
    assert [row["value"] for row in result["rows"]] == ["0.1", "0.123456789", "0.4"]
    assert all(isinstance(row["value"], str) for row in result["rows"])
    for row in result["rows"]:
        citation = row["citation"]
        assert citation["source_doc"] == DOC and citation["source_page"] == 1
        assert citation["publication_event_id"] == event["event_id"]
        assert citation["content_sha256"] and citation["attribution"]
    assert DOC in result["answer"] and "p. 1" in result["answer"]
    assert result["interpretation"]["interpreter_version"] == "inflation-nl-v1"
    inferred = result["interpretation"]["intent"]
    assert inferred["selector"]["concept"] == "inflation_rate_yoy"
    assert inferred["selector"]["geography"] == "TG"
    assert inferred["period_start"] == "2023-01" and inferred["period_end"] == "2026-10"
    assert "since_ends_at_current_month" in result["interpretation"]["assumptions"]
    assert FRENCH not in response.text and ENGLISH not in response.text
    assert "chart" not in result and "sql" not in result
    assert result["request_id"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert (target.read_bytes(), artifact.read_bytes(), state(fixture)) == before


def test_truncation_and_empty_range_remain_honest_over_http(published_history):
    with TestClient(app_for(settings(published_history[0][0]))) as client:
        truncated = client.post("/v1/query", json=request(limit=1)).json()
        empty = client.post("/v1/query", json=request("Inflation au Togo en 2030 ?")).json()
    assert truncated["status"] == "truncated" and truncated["row_count"] == 1
    assert truncated["has_more"] is True and truncated["complete"] is False
    assert "result_truncated" in truncated["warnings"]
    assert len(truncated["rows"]) == 1
    assert empty["status"] == "empty" and empty["rows"] == []
    assert empty["complete"] is True and empty["has_more"] is False
    assert empty["answer"] and "chart" not in empty


def test_multiple_vintages_are_not_silently_merged_in_http(published_vintages):
    with TestClient(app_for(settings(published_vintages[0][0]))) as client:
        response = client.post("/v1/query", json=request())
    assert response.status_code == 200
    result = response.json()
    assert result["row_count"] == 4
    assert [(row["period"], row["release_id"]) for row in result["rows"][:2]] == [
        ("2023-01", 2), ("2023-01", 3),
    ]
    assert "multiple_vintages" in result["warnings"]
    assert result["rows"][1]["revision_status"] == "revised"
    assert "chart" not in result


@pytest.mark.parametrize("change", ["operator", "source", "qa", "review"])
def test_repeated_http_request_requires_fresh_proof(published_history, monkeypatch, change):
    fixture, _ = published_history
    target, engine, tables, artifact = fixture
    with TestClient(app_for(settings(target))) as client:
        assert client.post("/v1/query", json=request()).status_code == 200
        if change == "operator":
            monkeypatch.setenv("SIKA_REVIEWER_IDS", "test:revoked")
        elif change == "source":
            artifact.write_bytes(pdf_bytes(2))
        elif change == "qa":
            with engine.begin() as con:
                con.execute(tables["units"].update().values(symbol="changed"))
            assert quality.run_quality_checks(target, release_ids=[2])["status"] == "failed"
            with engine.begin() as con:
                con.execute(tables["units"].update().values(symbol="%"))
        else:
            with engine.begin() as con:
                con.execute(tables["review_decisions"].insert().values(
                    release_id=2, reviewer="test:reviewer", decision="reject",
                    reason="Revoke synthetic fixture visibility.",
                ))
        result = error(client.post("/v1/query", json=request()), 503, "publication_unavailable")
    assert str(target) not in json.dumps(result) and "test:reviewer" not in json.dumps(result)


def test_invalid_sibling_cohort_denies_otherwise_valid_http_rows(published_vintages):
    fixture, _ = published_vintages
    target, engine, tables, _ = fixture
    with engine.begin() as con:
        con.execute(tables["review_decisions"].insert().values(
            release_id=3, reviewer="test:reviewer", decision="reject",
            reason="Revoke synthetic sibling publication.",
        ))
    with TestClient(app_for(settings(target))) as client:
        error(client.post("/v1/query", json=request("Inflation au Togo en mai 2026 ?")),
              503, "publication_unavailable")


def test_draft_fixture_does_not_become_a_public_catalog(ready):
    with TestClient(app_for(settings(ready[0]))) as client:
        response = client.post("/v1/query", json=request())
    error(response, 422, "unknown_series")
    assert "test.inflation" not in response.text
    assert state(ready)["events"] == 0


def test_api_ambiguity_hides_unproved_keys(ready, monkeypatch):
    target, engine, tables, _ = ready
    # Register only this invented test document, not a changed official profile.
    monkeypatch.setitem(quality.PROFILES, "synthetic_second_method.pdf",
                        quality.PROFILES[DOC] | {"variant": "test:second-method"})
    with engine.begin() as con:
        dimensions = dict(con.execute(sa.select(tables["dimensions"])).mappings().one())
        con.execute(tables["dimensions"].insert().values(dimensions | {
            "id": 2, "source_variant": "test:second-method",
        }))
        series = dict(con.execute(sa.select(tables["series"])).mappings().one())
        con.execute(tables["series"].insert().values(series | {
            "id": 2, "series_key": "test.secret.second-method", "dimensions_id": 2,
        }))
        row = dict(con.execute(sa.select(tables["observations"])
                               .where(tables["observations"].c.id == 2)).mappings().one())
        release = dict(con.execute(sa.select(tables["releases"])
                                   .where(tables["releases"].c.id == 2)).mappings().one())
        # A separate synthetic publication has its own methodology. Never change
        # a reviewed official-source mapping just to make a fixture pass QA.
        con.execute(tables["releases"].insert().values(release | {
            "id": 3, "release_key": "test.second-method",
            "source_doc": "synthetic_second_method.pdf", "status": "draft",
        }))
        con.execute(tables["observations"].insert().values(row | {
            "id": 3, "series_id": 2, "release_id": 3,
        }))
    publish_synthetic(ready, 2)
    publish_synthetic(ready, 3)
    with TestClient(app_for(settings(target))) as client:
        response = client.post("/v1/query", json=request())
        error(response, 422, "ambiguous_series")
        assert "test.secret" not in response.text
        narrowed = client.post("/v1/query", json=request(filters={
            "source_variant": "test:second-method",
        }))
    assert narrowed.status_code == 200 and narrowed.json()["row_count"] == 1
    assert narrowed.json()["series"]["source_variant"] == "test:second-method"


@pytest.mark.parametrize("payload", [
    None, [], "Inflation", 5, {}, request(version=True), request(version="1"),
    request(limit=True), request(limit="2"), request(limit=0), request(limit=501),
    request(question="x"), request(question=" " * 8), request(question="a" * 1001),
    request(question="Inflation\x00au Togo en 2026"), request(language="de"),
    request(sql="SELECT * FROM observations"), request(target="private.db"),
    request(source_root="data/raw"), request(proof={"approved": True}),
    request(catalog={"series_key": "private"}), request(execution_limits={"max_releases": 1000}),
    request(filters={"geography": "TG"}), request(filters={"source_variant": "x' OR 1=1--"}),
    request(filters={"price_basis": "2014"}),
])
def test_invalid_http_contract_has_no_numeric_or_execution_side_effect(transport_app, payload):
    with TestClient(transport_app) as client:
        error(client.post("/v1/query", content=json.dumps(payload),
                          headers={"Content-Type": "application/json"}), 422, "invalid_question")


@pytest.mark.parametrize("question,code", [
    ("Inflation", "clarification_required"),
    ("Inflation au Togo", "clarification_required"),
    ("Inflation depuis 2023", "clarification_required"),
    ("Inflation au Togo en 2026 sans les aliments", "invalid_period"),
    ("Inflation au Togo en 2026 ; SELECT * FROM observations", "invalid_period"),
    ("Quel sera le PIB du Togo en 2030 ?", "unsupported_question"),
    ("Forecast inflation in Togo for 2030", "unsupported_question"),
    ("Inflation au Togo en 2026-13", "invalid_period"),
    ("Inflation au Togo de 2026 a 2023", "invalid_period"),
    ("Inflation au Togo depuis 1900", "period_range_too_large"),
])
def test_semantic_refusal_consumes_entire_question_before_execution(
        transport_app, question, code):
    with TestClient(transport_app) as client:
        response = client.post("/v1/query", json=request(question))
    error(response, 422, code)
    assert question not in response.text


@pytest.mark.parametrize("body", [
    b"", b"{", b'{"question":"Inflation","question":"Inflation au Togo en 2026"}',
    b'{"question":"Inflation au Togo en 2026","filters":{"source_variant":"a","source_variant":"b"}}',
    b'{"question":NaN}', b'{"question":Infinity}', b'{"question":1e999}', b'\xff',
])
def test_malformed_json_duplicate_keys_and_nonfinite_numbers_are_rejected(transport_app, body):
    with TestClient(transport_app) as client:
        error(client.post("/v1/query", content=body,
                          headers={"Content-Type": "application/json"}), 400)


@pytest.mark.parametrize("headers", [
    {"Content-Type": "text/plain"}, {"Content-Type": "application/x-www-form-urlencoded"},
    {"Content-Type": "application/json; charset=latin-1"},
    {"Content-Type": "application/json", "Content-Encoding": "gzip"},
])
def test_unsupported_transport_encodings_are_rejected(transport_app, headers):
    with TestClient(transport_app) as client:
        error(client.post("/v1/query", content=json.dumps(request()), headers=headers), 415)


def test_utf8_json_charset_is_accepted(published_history):
    with TestClient(app_for(settings(published_history[0][0]))) as client:
        response = client.post("/v1/query", content=json.dumps(request(), ensure_ascii=False).encode(),
                               headers={"Content-Type": "application/json; charset=utf-8"})
    assert response.status_code == 200 and response.json()["language"] == "fr"


def test_actual_body_bytes_not_just_question_length_are_bounded(transport_app):
    # Whitespace can inflate an otherwise valid JSON object past the transport cap.
    body = b" " * 8193 + json.dumps(request()).encode()
    with TestClient(transport_app) as client:
        error(client.post("/v1/query", content=body,
                          headers={"Content-Type": "application/json"}), 413)


async def asgi_request(app, chunks, headers=(), disconnect=False):
    """Exercise raw streamed bytes without client-normalized Content-Length."""
    messages = []
    incoming = [{"type": "http.request", "body": body, "more_body": i < len(chunks) - 1}
                for i, body in enumerate(chunks)]
    if disconnect:
        incoming = [{"type": "http.disconnect"}]

    async def receive():
        if incoming:
            return incoming.pop(0)
        await asyncio.Future()

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": "/v1/query", "raw_path": b"/v1/query",
             "root_path": "", "query_string": b"", "headers": list(headers),
             "client": ("127.0.0.1", 12345), "server": ("testserver", 80)}
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages
                    if message["type"] == "http.response.body")
    return start["status"], json.loads(body), dict(start["headers"])


@pytest.mark.parametrize("length", [None, b"1", b"9000"])
def test_streamed_body_limit_applies_even_when_content_length_lies(transport_app, length):
    headers = [(b"content-type", b"application/json")]
    if length is not None:
        headers.append((b"content-length", length))
    status, result, _ = asyncio.run(asgi_request(transport_app, [b" " * 4096, b" " * 4097], headers))
    assert status == 413 and result["rows"] == []


@pytest.mark.parametrize("length", [b"-1", b"abc", b"1,2"])
def test_invalid_declared_length_is_rejected(transport_app, length):
    status, result, _ = asyncio.run(asgi_request(transport_app, [json.dumps(request()).encode()], [
        (b"content-type", b"application/json"), (b"content-length", length),
    ]))
    assert status == 400 and result["rows"] == []


def test_default_router_is_disabled_and_environment_cannot_enable_it(tmp_path, monkeypatch):
    monkeypatch.setenv("SIKA_DATABASE_URL", str(tmp_path / "private.db"))
    monkeypatch.setenv("SIKA_QUERY_ENABLED", "true")
    monkeypatch.setenv("SIKA_QUERY_TARGET", str(tmp_path / "private.db"))
    with TestClient(app_for()) as client:
        error(client.post("/v1/query", json=request()), 503, "query_disabled")
    assert not (tmp_path / "private.db").exists()


def test_main_app_mount_does_not_shadow_disabled_query_route():
    from api.main import app
    with TestClient(app) as client:
        error(client.post("/v1/query", json=request()), 503, "query_disabled")


def test_fixture_settings_refuse_target_outside_fixture_root(tmp_path):
    inside = tmp_path / "allowed"
    inside.mkdir()
    outside = tmp_path / "outside.db"
    outside.touch()
    with pytest.raises(ValueError):
        build_query_router(QueryFixtureSettings(fixture_root=inside, target=outside, today=TODAY))


def test_missing_fixture_target_fails_without_creating_database(tmp_path):
    missing = tmp_path / "not-created.db"
    with TestClient(app_for(settings(missing))) as client:
        response = client.post("/v1/query", json=request())
    error(response, 503, "database_unavailable")
    assert not missing.exists() and str(missing) not in response.text


def test_final_wrapper_bytes_are_capped_not_only_core_result(published_history):
    target = published_history[0][0]
    core = execute_query(intent(period_end="2026-10"), target)
    cap = len(core.model_dump_json().encode("utf-8")) + 8
    assert 1024 < cap < 262144
    policy = ExecutionLimits(max_result_bytes=cap)
    with TestClient(app_for(settings(target, execution_limits=policy))) as client:
        response = client.post("/v1/query", json=request())
    error(response, 413, "response_too_large")
    assert len(response.content) <= cap


@asynccontextmanager
async def asynchronous_client(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://testserver") as client:
            yield client


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_cancelled_or_timed_out_request_does_not_release_active_worker_slot(
        published_history, monkeypatch, stop):
    fixture, _ = published_history
    result = execute_query(intent(), fixture[0])
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def controlled(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(5), "Test must release the controlled physical worker"
            return result
        finally:
            finished.set()

    monkeypatch.setattr(query_api, "execute_query", controlled)
    normal_wait_budget = query_api.HTTP_TIMEOUT_SECONDS
    if stop == "timeout":
        monkeypatch.setattr(query_api, "HTTP_TIMEOUT_SECONDS", 0.03)
    app = app_for(settings(fixture[0]))

    async def scenario():
        async with asynchronous_client(app) as client:
            pending = asyncio.create_task(client.post("/v1/query", json=request()))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                if stop == "cancel":
                    pending.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await pending
                else:
                    error(await pending, 504, "query_timeout")
                denied = await client.post("/v1/query", json=request())
                error(denied, 429)
                assert denied.headers["retry-after"] == "1"
                assert not finished.is_set()
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 2)
                if not pending.done():
                    await pending
            # Only the blocked request should use the artificial 30 ms timeout.
            # Recovery must test released capacity, not host scheduler speed.
            monkeypatch.setattr(query_api, "HTTP_TIMEOUT_SECONDS", normal_wait_budget)
            for _ in range(30):
                response = await client.post("/v1/query", json=request())
                if response.status_code != 429:
                    break
                await asyncio.sleep(0.01)
            assert response.status_code == 200

    asyncio.run(scenario())


@pytest.mark.parametrize("code,status", [
    ("invalid_catalog", 503), ("invalid_intent", 422),
    ("invalid_execution_limits", 503), ("query_timeout", 504),
    ("response_too_large", 413), ("resource_limit", 503),
    ("database_unavailable", 503), ("publication_unavailable", 503),
])
def test_service_errors_remain_sanitized_and_do_not_become_500(tmp_path, monkeypatch, code, status):
    target = tmp_path / "synthetic.db"
    target.touch()
    (tmp_path / "trust_core_sources").mkdir()

    def refused(*args, **kwargs):
        raise QueryError(code, "PRIVATE source text / PRIVATE path")

    monkeypatch.setattr(query_api, "execute_query", refused)
    with TestClient(app_for(settings(target))) as client:
        response = client.post("/v1/query", json=request(ENGLISH))
    result = error(response, status, code)
    assert result["language"] == "en"
    assert "PRIVATE" not in response.text and str(target) not in response.text


def test_unexpected_worker_failure_is_sanitized(tmp_path, monkeypatch):
    target = tmp_path / "synthetic.db"
    target.touch()
    (tmp_path / "trust_core_sources").mkdir()

    def broken(*args, **kwargs):
        raise RuntimeError("PRIVATE source text")

    monkeypatch.setattr(query_api, "execute_query", broken)
    with TestClient(app_for(settings(target))) as client:
        response = client.post("/v1/query", json=request())
    error(response, 500, "internal_error")
    assert "PRIVATE" not in response.text


def test_body_timeout_and_disconnect_fail_without_execution(transport_app, monkeypatch):
    monkeypatch.setattr(query_api, "BODY_TIMEOUT_SECONDS", 0.01)
    status, value, _ = asyncio.run(asgi_request(transport_app, [], [
        (b"content-type", b"application/json"),
    ]))
    assert status == 408 and value["error"]["code"] == "request_timeout"
    status, value, _ = asyncio.run(asgi_request(transport_app, [], [
        (b"content-type", b"application/json"),
    ], disconnect=True))
    assert status == 400 and value["rows"] == []


def test_duplicate_length_and_query_parameter_overrides_are_refused(transport_app):
    status, value, _ = asyncio.run(asgi_request(transport_app, [json.dumps(request()).encode()], [
        (b"content-type", b"application/json"),
        (b"content-length", b"10"), (b"content-length", b"10"),
    ]))
    assert status == 400 and value["rows"] == []
    with TestClient(transport_app) as client:
        error(client.post("/v1/query?target=private.db", json=request()), 422, "invalid_question")


@pytest.mark.parametrize("field", ["target", "source_root", "legacy"])
def test_known_database_aliases_are_rejected_in_every_path_field(tmp_path, monkeypatch, field):
    # Only synthetic stand-ins: do not create aliases to either real database.
    forbidden = tmp_path / "stand-in-real.db"
    forbidden.touch()
    alias = tmp_path / "alias.db"
    alias.hardlink_to(forbidden)
    monkeypatch.setattr(query_api, "_REAL_DATABASES", (forbidden,))
    target = tmp_path / "fixture.db"
    target.touch()
    arguments = {"fixture_root": tmp_path, "target": target, field: alias}
    with pytest.raises(ValueError, match="Invalid fixture"):
        build_query_router(QueryFixtureSettings(**arguments))


def test_runtime_alias_recheck_refuses_swapped_target(tmp_path, monkeypatch):
    forbidden = tmp_path / "stand-in-real.db"
    forbidden.touch()
    target = tmp_path / "fixture.db"
    target.touch()
    (tmp_path / "trust_core_sources").mkdir()
    monkeypatch.setattr(query_api, "_REAL_DATABASES", (forbidden,))
    app = app_for(settings(target))
    target.unlink()
    target.hardlink_to(forbidden)
    monkeypatch.setattr(query_api, "execute_query", lambda *a, **k: pytest.fail("Must refuse alias"))
    with TestClient(app) as client:
        error(client.post("/v1/query", json=request()), 503, "database_unavailable")


@pytest.mark.parametrize("field,value", [
    ("execution_limits", ExecutionLimits.model_construct(max_result_bytes=999999999)),
    ("today", "2026-10-15"), ("target", "fixture.db"),
])
def test_forged_settings_cannot_raise_limits_or_coerce_paths(tmp_path, field, value):
    arguments = {"fixture_root": tmp_path, "target": tmp_path / "fixture.db", field: value}
    with pytest.raises(ValueError, match="Invalid fixture"):
        build_query_router(QueryFixtureSettings(**arguments))
