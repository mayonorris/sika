"""Serialized, bounded publication reads on disposable synthetic fixtures."""

from dataclasses import fields
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import time

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sika import publication
from sika.database import create_database_engine
from test_publication import ready, authorized, approve_ready, approval_id, RELEASE_ID, OBSERVATION_ID
from test_quality import store, pdf_bytes


@pytest.fixture
def published(ready):
    approval, _, _ = approve_ready(ready)
    publication.publish(ready[0], RELEASE_ID, approval_id(approval), "Publish synthetic query fixture.")
    return ready


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_read_session_projects_one_snapshot_with_citations_without_writes(published, monkeypatch):
    target = published[0]
    before = digest(target)
    calls = []
    original = publication._inspect
    def inspected(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(publication, "_inspect", inspected)
    with publication.published_read(target) as session:
        assert len(session.snapshot_sha256) == 64
        assert session.deadline > time.monotonic()
        catalog = session.catalog_records()
        assert len(catalog) == 1
        assert catalog[0]["series_key"] == "test.inflation"
        assert catalog[0]["concept_aliases"] == ("Inflation annuelle",)
        assert session.release_ids("test.inflation") == (RELEASE_ID,)
        rows = session.projected_rows(RELEASE_ID)
        assert len(rows) == 1
        row = rows[0]
        assert row["observation_id"] == OBSERVATION_ID
        assert row["value"] == "0.4"
        assert row["unit"] == "percent"
        assert row["source_page"] == 1
        assert row["source_url"].startswith("https://")
        assert row["publisher"] == "INSEED"
        assert row["license_url"] == "https://example.test/synthetic-fixture-license"
        assert row["published_on"] == "2026-06-15"
        assert row["publication_event_id"] > 0
        assert row["content_sha256"] == digest(published[3])
        assert publication.published_rows(target, RELEASE_ID, _session=session)[0]["id"] == OBSERVATION_ID
    assert calls == [1]
    assert digest(target) == before


@pytest.mark.parametrize("access", [
    lambda session: session.catalog_records(), lambda session: session.release_ids("test.inflation"),
    lambda session: session.projected_rows(RELEASE_ID), lambda session: session.check_budget(),
    lambda session: session.snapshot_sha256, lambda session: session.deadline,
])
def test_closed_session_cannot_be_reused(published, access):
    with publication.published_read(published[0]) as session:
        assert session.projected_rows(RELEASE_ID)
    with pytest.raises(publication.PublicationError, match="no longer active"):
        access(session)


def test_session_is_bound_to_target_legacy_and_archive(published):
    with publication.published_read(published[0]) as session:
        with pytest.raises(publication.PublicationError, match="another target"):
            publication.published_rows(published[0], RELEASE_ID, source_root=published[0].parent, _session=session)
        with pytest.raises(publication.PublicationError, match="another target"):
            publication.published_rows(published[0], RELEASE_ID, legacy=published[0], _session=session)


def test_draft_catalog_and_rows_are_not_exposed(store):
    with publication.published_read(store[0]) as session:
        assert session.catalog_records() == ()
        assert session.release_ids("test.inflation") == ()
        assert session.projected_rows(1) == []


def test_later_rejection_is_not_hidden_by_published_status(published):
    with published[1].begin() as con:
        con.execute(published[2]["review_decisions"].insert().values(
            release_id=RELEASE_ID, reviewer="test:reviewer", decision="reject", reason="Withdraw synthetic review."))
    with publication.published_read(published[0]) as session:
        assert session.release_ids("test.inflation") == (RELEASE_ID,)
        with pytest.raises(publication.PublicationError, match="invalidates"):
            session.projected_rows(RELEASE_ID)


def test_current_authorization_required_per_request(published, monkeypatch):
    monkeypatch.setenv("SIKA_REVIEWER_IDS", "test:new-reviewer")
    with publication.published_read(published[0]) as session:
        with pytest.raises(publication.PublicationError, match="no longer authorized"):
            session.projected_rows(RELEASE_ID)


def test_changed_allowlist_during_read_refuses_whole_context(published, monkeypatch):
    with pytest.raises(publication.PublicationError, match="authorization changed"):
        with publication.published_read(published[0]) as session:
            assert session.projected_rows(RELEASE_ID)
            monkeypatch.setenv("SIKA_REVIEWER_IDS", "test:reviewer,test:extra")


def test_changed_artifact_during_read_refuses_whole_context(published):
    original = published[3].read_bytes()
    try:
        with pytest.raises(publication.PublicationError, match="Source artifact changed"):
            with publication.published_read(published[0]) as session:
                assert session.projected_rows(RELEASE_ID)
                published[3].write_bytes(pdf_bytes(2))
    finally:
        published[3].write_bytes(original)


def test_query_only_refuses_writes_and_holds_writer_reservation(published):
    target = published[0]
    with sqlite3.connect(target) as external:
        external.execute("PRAGMA journal_mode=WAL")
    with publication.published_read(target) as session:
        with pytest.raises(DBAPIError, match="readonly"):
            session._con.exec_driver_sql("UPDATE units SET symbol='invalid'")
        with sqlite3.connect(target, timeout=0.01) as concurrent:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                concurrent.execute("UPDATE units SET symbol='invalid'")
        assert session.projected_rows(RELEASE_ID)


def test_lock_wait_is_bounded_and_sanitized(published):
    target = published[0]
    with sqlite3.connect(target) as concurrent:
        concurrent.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        with pytest.raises(publication.ReadLimitError) as error:
            with publication.published_read(target, limits=publication.ReadLimits(lock_timeout_ms=10)):
                pytest.fail("The reserved database should not be entered")
        assert error.value.code == "database_unavailable"
        assert time.monotonic() - started < 1
        assert str(target) not in str(error.value)


@pytest.mark.parametrize("field", [field.name for field in fields(publication.ReadLimits)])
@pytest.mark.parametrize("bad", [0, -1, True, "1", 1.1, None])
def test_read_limits_refuse_coercion_and_unbounded_values(field, bad):
    with pytest.raises(ValueError):
        publication.ReadLimits(**{field: bad})


@pytest.mark.parametrize("field", [field.name for field in fields(publication.ReadLimits)])
def test_read_limits_cannot_exceed_ceiling(field):
    with pytest.raises(ValueError):
        publication.ReadLimits(**{field: getattr(publication.ReadLimits(), field) + 1})


@pytest.mark.parametrize("limits", [
    publication.ReadLimits(max_rows=1), publication.ReadLimits(max_bytes=1),
    publication.ReadLimits(max_cell_bytes=1), publication.ReadLimits(max_tables=1),
    publication.ReadLimits(max_artifact_bytes=1), publication.ReadLimits(max_total_artifact_bytes=1),
])
def test_resource_preflight_runs_before_inspection(published, monkeypatch, limits):
    def forbidden(*args, **kwargs):
        pytest.fail("Oversized inputs must not reach snapshot/artifact inspection")
    monkeypatch.setattr(publication, "_inspect", forbidden)
    with pytest.raises(publication.ReadLimitError) as error:
        with publication.published_read(published[0], limits=limits):
            pytest.fail("Oversized fixture must be refused")
    assert error.value.code == "resource_limit"


def test_legacy_file_size_is_checked_before_reading(published, tmp_path, monkeypatch):
    legacy = tmp_path / "synthetic_legacy.db"
    legacy.write_bytes(b"synthetic bytes not a database")
    def forbidden(*args, **kwargs):
        pytest.fail("Oversized legacy file must not be read")
    monkeypatch.setattr(publication, "read_snapshot", forbidden)
    with pytest.raises(publication.ReadLimitError) as error:
        with publication.published_read(published[0], legacy=legacy, limits=publication.ReadLimits(max_legacy_bytes=1)):
            pytest.fail("Oversized fixture must be refused")
    assert error.value.code == "resource_limit"


def test_expired_deadline_refuses_before_database_open(published, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Expired requests must not open a database")
    monkeypatch.setattr(publication, "create_database_engine", forbidden)
    with pytest.raises(publication.ReadLimitError) as error:
        with publication.published_read(published[0], deadline=time.monotonic() - 1):
            pytest.fail("Expired fixture must be refused")
    assert error.value.code == "query_timeout"


def test_vm_step_budget_refuses_with_resource_code(published):
    with pytest.raises(publication.ReadLimitError) as error:
        with publication.published_read(published[0], limits=publication.ReadLimits(sql_steps=1)):
            pytest.fail("An exhausted SQL budget must not enter the service")
    assert error.value.code == "resource_limit"


def test_interrupted_read_releases_lock_and_does_not_leave_write_capability(published):
    with pytest.raises(RuntimeError, match="interrupted"):
        with publication.published_read(published[0]) as session:
            assert session.projected_rows(RELEASE_ID)
            raise RuntimeError("Synthetic read interrupted")
    with published[1].begin() as con:
        con.execute(published[2]["units"].update().values(symbol="%"))
        with pytest.raises(DBAPIError):
            con.execute(published[2]["releases"].update().where(published[2]["releases"].c.id == 1)
                        .values(status="published"))


@pytest.mark.parametrize("mode", [None, "", "DEFERRED", "exclusive", True, [], {}])
def test_database_engine_rejects_invalid_begin_modes(mode):
    with pytest.raises(ValueError):
        create_database_engine("sqlite:///:memory:", sqlite_begin=mode)


@pytest.mark.parametrize("mode,expected", [("deferred", "BEGIN"), ("immediate", "BEGIN IMMEDIATE")])
def test_database_engine_explicit_begin_mode(mode, expected):
    engine = create_database_engine("sqlite:///:memory:", sqlite_begin=mode)
    statements = []
    @sa.event.listens_for(engine, "before_cursor_execute")
    def observe(con, cursor, statement, parameters, context, many):
        statements.append(statement)
    try:
        with engine.connect() as con:
            con.execute(sa.text("SELECT 1"))
        assert statements[0] == expected
    finally:
        engine.dispose()


def legacy_file(tmp_path, *, rows=3, label_size=20, view=False):
    """Minimal unrelated synthetic legacy data; no official figures copied."""
    path = tmp_path / "legacy_reader.db"
    with closing(sqlite3.connect(path)) as con:
        if view:
            con.execute("CREATE VIEW observations AS SELECT 1 AS id, 'inflation' AS indicator, 'synthetic.pdf' AS source_doc")
        else:
            con.execute("CREATE TABLE observations(id INTEGER PRIMARY KEY,indicator TEXT,source_doc TEXT,indicator_label TEXT)")
            con.executemany("INSERT INTO observations VALUES(?,?,?,?)", [
                (i, "inflation_rate_yoy" if i % 2 else "synthetic_gdp", "synthetic.pdf", "x" * label_size)
                for i in range(1, rows + 1)])
        con.execute("CREATE TABLE passages(id INTEGER PRIMARY KEY, source_doc TEXT,text TEXT)")
        con.executemany("INSERT INTO passages VALUES(?,?,?)", [(1, "synthetic.pdf", "Synthetic source text"),
                                                               (2, "other.pdf", "Unrelated synthetic text")])
        con.commit()
    return path


def test_bounded_legacy_reader_preserves_exact_snapshot_semantics(tmp_path):
    path = legacy_file(tmp_path)
    before = digest(path)
    limits = publication.ReadLimits()
    budget = publication._ReadBudget(limits, time.monotonic() + 5)
    assert publication._bounded_legacy_snapshot(path, limits, budget) == publication.read_snapshot(path)
    assert digest(path) == before


@pytest.mark.parametrize("limits,rows,label_size", [
    (publication.ReadLimits(max_rows=1), 3, 20),
    (publication.ReadLimits(max_cell_bytes=10), 3, 20),
    (publication.ReadLimits(max_bytes=50), 3, 20),
])
def test_legacy_rows_and_cells_are_bounded_before_scope_filter(tmp_path, monkeypatch, limits, rows, label_size):
    path = legacy_file(tmp_path, rows=rows, label_size=label_size)
    assert path.stat().st_size < limits.max_legacy_bytes
    def forbidden(*args, **kwargs):
        pytest.fail("Oversized legacy must not reach scope filtering/materialization")
    monkeypatch.setattr(publication, "in_scope", forbidden)
    budget = publication._ReadBudget(limits, time.monotonic() + 5)
    with pytest.raises(publication.ReadLimitError) as error:
        publication._bounded_legacy_snapshot(path, limits, budget)
    assert error.value.code == "resource_limit"


def test_legacy_views_are_not_accepted_as_bounded_tables(tmp_path):
    path = legacy_file(tmp_path, view=True)
    limits = publication.ReadLimits()
    budget = publication._ReadBudget(limits, time.monotonic() + 5)
    with pytest.raises(publication.PublicationError, match="physical tables"):
        publication._bounded_legacy_snapshot(path, limits, budget)


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_bounded_legacy_rejects_active_journal(tmp_path, suffix):
    path = legacy_file(tmp_path)
    Path(str(path) + suffix).write_bytes(b"Synthetic active journal marker")
    limits = publication.ReadLimits()
    budget = publication._ReadBudget(limits, time.monotonic() + 5)
    with pytest.raises(publication.PublicationError, match="checkpointed"):
        publication._bounded_legacy_snapshot(path, limits, budget)


def test_legacy_preflight_uses_shared_vm_budget_and_closes_after_refusal(tmp_path):
    path = legacy_file(tmp_path, rows=1000)
    before = digest(path)
    limits = publication.ReadLimits(sql_steps=1)
    budget = publication._ReadBudget(limits, time.monotonic() + 5)
    with pytest.raises(publication.ReadLimitError) as error:
        publication._bounded_legacy_snapshot(path, limits, budget)
    assert error.value.code == "resource_limit"
    assert budget.steps >= 1000
    assert digest(path) == before
    with closing(sqlite3.connect(path, timeout=0.01)) as con:
        con.execute("BEGIN IMMEDIATE")
        con.rollback()
