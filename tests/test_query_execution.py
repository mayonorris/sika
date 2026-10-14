"""Execute typed queries only against genuine, disposable publication fixtures.

All observations and approval assertions in this module are synthetic. No real
database, source certification, provider, API route or publication state is used.
"""

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import itertools
import json
import re
import socket
import sqlite3

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from sika import publication, quality, query_execution
from sika.query import QueryError, QueryIntent, SeriesCatalog, compile_query
from sika.query_execution import ExecutionLimits, execute_query, query_response
from test_publication import authorized, ready, approval_id, state
from test_quality import DOC, pdf_bytes, store


def intent(**updates):
    """Structured equivalent of GQ1, not a natural-language interpreter."""
    return {
        "version": 1,
        "operation": "series",
        "selector": {"series_key": "test.inflation"},
        "period_start": "2023-01",
        "period_end": "2026-06",
        "limit": 100,
    } | updates


def publish_synthetic(fixture, release_id):
    """Use real quality/review/publication services for exactly the fixture IDs."""
    target, engine, tables, _ = fixture
    report = quality.run_quality_checks(target, release_ids=[release_id])["releases"][0]
    assert report["status"] == "passed"
    with engine.connect() as con:
        identifiers = con.execute(sa.select(tables["observations"].c.id)
                                  .where(tables["observations"].c.release_id == release_id)
                                  .order_by(tables["observations"].c.id)).scalars().all()
    evidence = {
        "source_values": "All listed values are synthetic and verified within this test fixture.",
        "revision_marks": "Final revision flags belong to this synthetic fixture only.",
        "reuse_rights": "The synthetic fixture is explicitly reusable for tests.",
        "verified_observation_ids": identifiers,
        "warnings": {finding["code"]: "Acknowledged synthetic test limitation."
                     for finding in report["findings"] if finding["severity"] == "WARN"},
    }
    approved = publication.approve(target, release_id, report["quality_run_id"],
                                   "Review the entire synthetic fixture cohort.", evidence)
    return publication.publish(target, release_id, approval_id(approved),
                               "Publish the reviewed synthetic fixture for a test.")


def add_history(fixture):
    """Add decimal-bearing history before QA and review, never after publication."""
    _, engine, tables, _ = fixture
    with engine.begin() as con:
        original = dict(con.execute(sa.select(tables["observations"])
                                    .where(tables["observations"].c.id == 2)).mappings().one())
        con.execute(tables["observations"].insert(), [
            original | {"id": 3, "period": "2023-01", "value": Decimal("0.1"),
                        "source_locator": "synthetic table, row 1"},
            original | {"id": 4, "period": "2024-02", "value": Decimal("0.1234567890"),
                        "source_locator": "synthetic table, row 2"},
        ])


def add_vintage(fixture):
    """Declare a revised synthetic vintage while every affected row is draft."""
    _, engine, tables, _ = fixture
    with engine.begin() as con:
        release = dict(con.execute(sa.select(tables["releases"])
                                   .where(tables["releases"].c.id == 2)).mappings().one())
        con.execute(tables["releases"].insert().values(release | {
            "id": 3, "release_key": "test.second-published-vintage", "status": "draft",
            "supersedes_id": 2,
        }))
        original = dict(con.execute(sa.select(tables["observations"])
                                    .where(tables["observations"].c.id == 3)).mappings().one())
        con.execute(tables["observations"].insert().values(original | {
            "id": 5, "release_id": 3, "revision_status": "revised", "value": Decimal("0.2"),
        }))


@pytest.fixture
def published_history(ready):
    add_history(ready)
    event = publish_synthetic(ready, 2)
    return ready, event


@pytest.fixture
def published_vintages(ready):
    add_history(ready)
    add_vintage(ready)
    events = (publish_synthetic(ready, 2), publish_synthetic(ready, 3))
    return ready, events


def assert_error(response, code):
    assert response["version"] == 1
    assert response["status"] == "error"
    assert response["rows"] == []
    assert response["row_count"] == 0
    assert response["has_more"] is False
    assert response["error"]["code"] == code
    assert response["error"]["message"]
    assert "value" not in response and "series" not in response


def test_structured_golden_question_returns_sorted_exact_cited_decimals(published_history):
    fixture, event = published_history
    result = execute_query(intent(), fixture[0])
    assert result.version == 1
    assert result.status == "ok"
    assert result.row_count == 3
    assert result.complete is True and result.has_more is False
    assert result.series.series_key == "test.inflation"
    assert result.series.concept == "inflation_rate_yoy"
    assert result.series.geography == "TG"
    assert (result.period_start, result.period_end) == ("2023-01", "2026-06")
    assert [(row.observation_id, row.period) for row in result.rows] == [
        (3, "2023-01"), (4, "2024-02"), (2, "2026-05"),
    ]
    assert [row.value for row in result.rows] == [
        Decimal("0.1"), Decimal("0.1234567890"), Decimal("0.4"),
    ]
    assert all(isinstance(row.value, Decimal) for row in result.rows)
    for row in result.rows:
        assert row.series_key == result.series.series_key
        assert row.unit == "percent"
        assert row.release_id == 2 and row.revision_status == "final"
        assert row.citation.source_doc == DOC
        assert row.citation.source_page == 1
        assert row.citation.source_url.startswith("https://")
        assert row.citation.publisher == "INSEED"
        assert "Synthetic" in row.citation.attribution
        assert row.citation.license_url == "https://example.test/synthetic-fixture-license"
        assert row.citation.release_key == "test.verified-vintage"
        assert row.citation.published_on == "2026-06-15"
        assert row.citation.content_sha256 == hashlib.sha256(fixture[3].read_bytes()).hexdigest()
        assert row.citation.publication_event_id == event["event_id"]
    assert result.rows[0].citation.source_locator == "synthetic table, row 1"
    assert re.fullmatch(r"[a-f0-9]{64}", result.snapshot_sha256)
    as_of = datetime.fromisoformat(result.as_of_utc.replace("Z", "+00:00"))
    assert as_of.utcoffset() == timezone.utc.utcoffset(as_of)


def test_response_keeps_decimal_strings_not_binary_float_values(published_history):
    response = query_response(intent(), published_history[0][0])
    assert response["status"] == "ok"
    assert all(type(row["value"]) is str for row in response["rows"])
    assert [Decimal(row["value"]) for row in response["rows"]] == [
        Decimal("0.1"), Decimal("0.1234567890"), Decimal("0.4"),
    ]
    assert json.loads(json.dumps(response))["rows"][1]["value"] == response["rows"][1]["value"]


def test_inclusive_bounds_filter_without_inventing_missing_periods(published_history):
    result = execute_query(intent(period_start="2024-02", period_end="2026-05"),
                           published_history[0][0])
    assert [row.period for row in result.rows] == ["2024-02", "2026-05"]
    assert result.row_count == 2 and result.complete is True


def test_no_data_is_an_empty_cited_series_not_a_forecast(published_history):
    result = execute_query(intent(period_start="2030-01", period_end="2030-12"),
                           published_history[0][0])
    assert result.status == "empty"
    assert result.rows == () and result.row_count == 0
    assert result.has_more is False and result.complete is True
    assert result.series.series_key == "test.inflation"
    assert query_response(intent(period_start="2030-01", period_end="2030-12"),
                          published_history[0][0])["rows"] == []


@pytest.mark.parametrize("limit,expected,truncated", [(1, 1, True), (2, 2, True),
                                                       (3, 3, False), (4, 3, False)])
def test_limit_plus_one_sentinel_is_removed_and_truncation_disclosed(
        published_history, limit, expected, truncated):
    result = execute_query(intent(limit=limit), published_history[0][0])
    assert len(result.rows) == result.row_count == expected
    assert result.has_more is truncated
    assert result.complete is not truncated
    assert result.status == ("truncated" if truncated else "ok")
    assert ("result_truncated" in result.warnings) is truncated
    assert [row.period for row in result.rows] == ["2023-01", "2024-02", "2026-05"][:expected]


def test_all_vintages_survive_in_stable_period_release_observation_order(published_vintages):
    fixture, events = published_vintages
    result = execute_query(intent(), fixture[0])
    assert result.row_count == 4
    assert [(row.period, row.release_id, row.observation_id) for row in result.rows] == [
        ("2023-01", 2, 3), ("2023-01", 3, 5), ("2024-02", 2, 4), ("2026-05", 2, 2),
    ]
    assert [row.value for row in result.rows[:2]] == [Decimal("0.1"), Decimal("0.2")]
    assert result.rows[1].revision_status == "revised"
    assert "multiple_vintages" in result.warnings
    assert {row.citation.publication_event_id for row in result.rows} == {
        event["event_id"] for event in events
    }


def test_repeat_reads_have_fresh_as_of_but_stable_rows_and_snapshot(published_history):
    target = published_history[0][0]
    first = execute_query(intent(), target)
    second = execute_query(intent(), target)
    assert first.rows == second.rows
    assert first.series == second.series
    assert first.snapshot_sha256 == second.snapshot_sha256
    assert second.as_of_utc >= first.as_of_utc


def test_execution_leaves_source_database_bytes_states_and_artifact_unchanged(published_history):
    fixture, _ = published_history
    target, _, _, artifact = fixture
    before = target.read_bytes(), artifact.read_bytes(), state(fixture)
    execute_query(intent(), target)
    query_response(intent(limit=1), target)
    assert (target.read_bytes(), artifact.read_bytes(), state(fixture)) == before


def test_query_execution_uses_no_network_provider_or_env_file(published_history, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A protected local fixture query must not make a network request")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://must-never-be-contacted.example.test")
    assert execute_query(intent(), published_history[0][0]).row_count == 3


def test_all_draft_catalog_is_not_exposed_as_public_identity(ready):
    assert_error(query_response(intent(), ready[0]), "unknown_series")
    assert state(ready)["events"] == 0


def test_reviewed_but_unpublished_series_is_not_queryable(ready):
    from test_publication import approve_ready
    approve_ready(ready)
    assert_error(query_response(intent(), ready[0]), "unknown_series")
    assert state(ready)["releases"] == [(1, "draft"), (2, "reviewed")]


def test_unknown_rights_do_not_leak_draft_rows(store, authorized):
    assert quality.run_quality_checks(store[0])["status"] == "passed"
    assert_error(query_response(intent(), store[0]), "unknown_series")
    assert state(store)["events"] == 0


@pytest.mark.parametrize("change", [
    "revoked_operator", "later_release_review", "later_observation_review", "later_qa",
    "catalog", "source_rights", "source_attribution", "source_artifact", "missing_artifact",
])
def test_fresh_proof_refuses_changed_authorization_and_evidence(
        published_history, monkeypatch, change):
    fixture, _ = published_history
    target, engine, tables, artifact = fixture
    assert execute_query(intent(), target).row_count == 3
    if change == "revoked_operator":
        monkeypatch.setenv("SIKA_REVIEWER_IDS", "test:different-reviewer")
    elif change in {"later_release_review", "later_observation_review"}:
        with engine.begin() as con:
            con.execute(tables["review_decisions"].insert().values(
                release_id=2 if change == "later_release_review" else None,
                observation_id=3 if change == "later_observation_review" else None,
                reviewer="test:reviewer", decision="reject", reason="Withdraw synthetic visibility.",
            ))
    elif change == "later_qa":
        # A later failed technical review remains authoritative after restoration.
        with engine.begin() as con:
            con.execute(tables["units"].update().values(symbol="invalid"))
        assert quality.run_quality_checks(target, release_ids=[2])["status"] == "failed"
        with engine.begin() as con:
            con.execute(tables["units"].update().values(symbol="%"))
    elif change == "catalog":
        with engine.begin() as con:
            con.execute(tables["series"].update().values(label_fr="Changed synthetic label"))
    elif change == "source_rights":
        with engine.begin() as con:
            con.execute(tables["sources"].update().values(reuse_status="restricted"))
    elif change == "source_attribution":
        with engine.begin() as con:
            con.execute(tables["sources"].update().values(attribution="Changed synthetic attribution"))
    elif change == "source_artifact":
        artifact.write_bytes(pdf_bytes(2))
    elif change == "missing_artifact":
        artifact.unlink()
    response = query_response(intent(), target)
    assert_error(response, "publication_unavailable")
    assert "test:reviewer" not in response["error"]["message"]
    assert str(target) not in response["error"]["message"]


def test_an_invalid_contributing_vintage_never_returns_a_partial_valid_answer(published_vintages):
    fixture, _ = published_vintages
    target, engine, tables, _ = fixture
    with engine.begin() as con:
        con.execute(tables["review_decisions"].insert().values(
            release_id=3, reviewer="test:reviewer", decision="reject",
            reason="Withdraw one synthetic vintage only.",
        ))
    assert len(publication.published_rows(target, 2)) == 3
    # Even a period with no values in vintage 3 must validate the full contributor.
    assert_error(query_response(intent(period_start="2026-05", period_end="2026-05"), target),
                 "publication_unavailable")


def test_privileged_disposable_database_partial_cohort_is_denied(published_history):
    fixture, _ = published_history
    target = fixture[0]
    # File/DB owners are privileged and outside the production write threat model.
    # Corrupt only this disposable pytest DB to verify protected reads still refuse
    # an incomplete cohort rather than presenting its remaining rows as certified.
    with sqlite3.connect(target) as con:
        triggers = con.execute("SELECT name FROM sqlite_master WHERE type='trigger' "
                               "AND tbl_name='observations'").fetchall()
        for (name,) in triggers:
            assert re.fullmatch(r"[a-zA-Z0-9_]+", name)
            con.execute(f'DROP TRIGGER "{name}"')
        con.execute("UPDATE observations SET status='draft' WHERE id=3")
    assert_error(query_response(intent(), target), "publication_unavailable")


@pytest.mark.parametrize("payload", [
    None,
    [],
    {"sql": "SELECT * FROM observations"},
    intent(sql="SELECT * FROM observations"),
    intent(operation="forecast"),
    intent(version=True),
    intent(limit=True),
    intent(limit="2"),
    intent(limit=0),
    intent(limit=501),
    intent(selector={"series_key": "test.inflation", "concept": "inflation_rate_yoy"}),
    intent(selector={"series_key": "test.inflation\x00"}),
    intent(catalog={"series_key": "trusted-by-client"}),
    intent(rows=[{"value": 999}]),
    intent(proof={"approved": True}),
    intent(session="caller-owned-session"),
])
def test_invalid_intent_fails_before_any_database_or_archive_access(tmp_path, monkeypatch, payload):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid contract input must fail before protected I/O")
    monkeypatch.setattr(publication, "published_read", forbidden)
    missing = tmp_path / "do-not-create.db"
    assert_error(query_response(payload, missing), "invalid_intent")
    assert not missing.exists()


def test_forged_intent_and_prepared_query_never_grant_execution_authority(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unvalidated caller objects must fail before I/O")
    monkeypatch.setattr(publication, "published_read", forbidden)
    forged = QueryIntent.model_construct(**intent(limit="500"))
    assert_error(query_response(forged, tmp_path / "missing.db"), "invalid_intent")
    catalog = SeriesCatalog([{
        "series_key": "test.inflation", "concept": "inflation_rate_yoy", "geography": "TG",
        "frequency": "monthly", "unit": "percent", "category": "all_items", "sector": "all_sectors",
        "aggregation_level": "total", "price_basis": "not_applicable",
        "seasonal_adjustment": "not_reported", "source_variant": "test:fixture",
    }])
    plan = compile_query(intent(), catalog)
    assert plan.execution_authorized is False
    assert_error(query_response(plan, tmp_path / "missing.db"), "invalid_intent")


@pytest.mark.parametrize("period_start,period_end,code", [
    ("2026-13", "2026-13", "invalid_period"),
    ("2026-Q1", "2026-Q2", "invalid_period"),
    ("2026-05", "2023-01", "invalid_period"),
    ("0000-01", "2026-06", "invalid_period"),
    ("2000-01", "2026-06", "period_range_too_large"),
])
def test_valid_contract_still_rejects_invalid_calendar_or_oversized_range(
        published_history, period_start, period_end, code):
    assert_error(query_response(intent(period_start=period_start, period_end=period_end),
                                published_history[0][0]), code)


def test_unknown_exact_key_or_injection_is_bound_not_executed(published_history):
    fixture, _ = published_history
    before = state(fixture)
    payload = intent(selector={"series_key": "test.inflation' OR 1=1 --"})
    assert_error(query_response(payload, fixture[0]), "unknown_series")
    assert state(fixture) == before


@pytest.mark.parametrize("field,value", [
    ("max_releases", True), ("max_releases", 0), ("max_releases", 17),
    ("max_authorized_rows", "100"), ("max_authorized_rows", 0), ("max_authorized_rows", 10001),
    ("max_result_bytes", True), ("max_result_bytes", 1023), ("max_result_bytes", 262145),
    ("sql_steps", 0), ("sql_steps", 1000001),
    ("timeout_ms", 0), ("timeout_ms", 5001),
])
def test_execution_limits_are_strict_and_cannot_raise_server_ceilings(field, value):
    with pytest.raises(ValidationError):
        ExecutionLimits.model_validate({field: value})


def test_forged_execution_limits_are_revalidated_before_protected_io(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid execution limits must fail before protected I/O")
    monkeypatch.setattr(publication, "published_read", forbidden)
    forged = ExecutionLimits.model_construct(max_releases=1000)
    assert_error(query_response(intent(), tmp_path / "missing.db", limits=forged),
                 "invalid_execution_limits")


def test_forged_nested_read_limits_are_revalidated_before_protected_io(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid nested read policy must fail before protected I/O")
    monkeypatch.setattr(publication, "published_read", forbidden)
    nested = publication.ReadLimits()
    object.__setattr__(nested, "max_rows", 10000000)
    forged = ExecutionLimits.model_construct(read_limits=nested)
    assert_error(query_response(intent(), tmp_path / "missing.db", limits=forged),
                 "invalid_execution_limits")


@pytest.mark.parametrize("field", [
    "max_rows", "max_bytes", "max_cell_bytes", "max_artifact_bytes", "max_total_artifact_bytes", "max_tables",
])
def test_nested_snapshot_budgets_fail_closed_before_returning_numbers(published_history, field):
    policy = ExecutionLimits(read_limits=publication.ReadLimits(**{field: 1}))
    assert_error(query_response(intent(), published_history[0][0], limits=policy), "resource_limit")


def test_memory_sql_step_cap_fails_and_releases_resources(published_history):
    target = published_history[0][0]
    assert_error(query_response(intent(), target, limits=ExecutionLimits(sql_steps=1)), "resource_limit")
    assert execute_query(intent(), target).row_count == 3


def test_deadline_timeout_is_structured_and_retains_no_lock(published_history, monkeypatch):
    target = published_history[0][0]
    ticks = itertools.count(start=0.0, step=0.001)
    # A deterministic monotonic clock crosses the one-millisecond deadline;
    # no sleep, slow-machine assumption or protected proof helper is replaced.
    with monkeypatch.context() as clock_patch:
        clock_patch.setattr(query_execution.time, "monotonic", lambda: next(ticks))
        assert_error(query_response(intent(), target, limits=ExecutionLimits(timeout_ms=1)), "query_timeout")
    assert execute_query(intent(), target).row_count == 3


@pytest.mark.parametrize("changed", ["artifact", "reviewer_allowlist"])
def test_final_context_check_denies_changes_after_sql_execution(
        published_history, monkeypatch, changed):
    fixture, _ = published_history
    original = query_execution._execute_memory

    def changed_after_read(*args, **kwargs):
        rows = original(*args, **kwargs)
        assert rows  # The final refusal must prevent an otherwise successful answer.
        if changed == "artifact":
            fixture[3].write_bytes(pdf_bytes(2))
        else:
            monkeypatch.setenv("SIKA_REVIEWER_IDS", "test:revoked-after-sql")
        return rows

    monkeypatch.setattr(query_execution, "_execute_memory", changed_after_read)
    assert_error(query_response(intent(), fixture[0]), "publication_unavailable")


def test_published_catalog_ambiguity_reveals_no_unproved_candidate_keys(ready):
    target, engine, tables, _ = ready
    with engine.begin() as con:
        dimensions = dict(con.execute(sa.select(tables["dimensions"])).mappings().one())
        con.execute(tables["dimensions"].insert().values(dimensions | {
            "id": 2, "category": "food", "aggregation_level": "group",
        }))
        series = dict(con.execute(sa.select(tables["series"])).mappings().one())
        con.execute(tables["series"].insert().values(series | {
            "id": 2, "series_key": "test.inflation.food", "dimensions_id": 2,
        }))
        observation = dict(con.execute(sa.select(tables["observations"])
                                       .where(tables["observations"].c.id == 2)).mappings().one())
        con.execute(tables["observations"].insert().values(observation | {
            "id": 3, "series_id": 2, "value": Decimal("0.2"),
        }))
    publish_synthetic(ready, 2)
    broad = intent(selector={"concept": "inflation_rate_yoy", "geography": "TG"})
    response = query_response(broad, target)
    assert_error(response, "ambiguous_series")
    assert "candidate_keys" not in response["error"]
    assert "test.inflation.food" not in json.dumps(response)
    narrowed = execute_query(intent(selector={"concept": "inflation_rate_yoy", "geography": "TG",
                                             "category": "food"}), target)
    assert narrowed.series.series_key == "test.inflation.food"
    assert [(row.observation_id, row.value) for row in narrowed.rows] == [(3, Decimal("0.2"))]


def test_authorized_row_cohort_cap_refuses_instead_of_silent_truncation(published_history):
    assert_error(query_response(intent(limit=1), published_history[0][0],
                                limits=ExecutionLimits(max_authorized_rows=1)), "resource_limit")


def test_contributing_release_cap_refuses_instead_of_dropping_a_vintage(published_vintages):
    assert_error(query_response(intent(), published_vintages[0][0],
                                limits=ExecutionLimits(max_releases=1)), "resource_limit")


def test_cited_serialized_response_size_cap_is_enforced(published_history):
    assert_error(query_response(intent(), published_history[0][0],
                                limits=ExecutionLimits(max_result_bytes=1024)), "response_too_large")


def test_missing_target_is_not_created_and_failure_discloses_no_path(tmp_path):
    missing = tmp_path / "private-directory" / "private.db"
    response = query_response(intent(), missing)
    assert_error(response, "database_unavailable")
    assert not missing.exists()
    assert "private-directory" not in json.dumps(response)


def test_legacy_or_unmigrated_database_cannot_be_a_publication_target(tmp_path):
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as con:
        con.execute("CREATE TABLE observations(id INTEGER PRIMARY KEY, value NUMERIC)")
        con.execute("INSERT INTO observations VALUES(1,999)")
    before = legacy.read_bytes()
    response = query_response(intent(), legacy, legacy=legacy)
    assert response["status"] == "error" and response["rows"] == []
    assert response["error"]["code"] in {"database_unavailable", "publication_unavailable"}
    assert legacy.read_bytes() == before


def test_failed_execution_releases_lock_for_a_later_fresh_read(published_history):
    fixture, _ = published_history
    target, engine, tables, _ = fixture
    assert_error(query_response(intent(), target, limits=ExecutionLimits(max_result_bytes=1024)),
                 "response_too_large")
    # A failed request must not retain BEGIN IMMEDIATE or a SQLite connection.
    with engine.begin() as con:
        con.execute(tables["geographies"].update().values(label_fr="Togo"))
    assert execute_query(intent(), target).row_count == 3
