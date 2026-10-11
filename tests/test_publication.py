"""Snapshot-bound operator review and default-deny publication contracts."""

from datetime import date
import sqlite3

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sika import publication, quality
from test_quality import store, pdf_bytes, DOC


RELEASE_ID = 2
OBSERVATION_ID = 2
POLICY_ERRORS = (ValueError, PermissionError)


@pytest.fixture
def authorized(monkeypatch):
    monkeypatch.setattr(publication, "operator_identity", lambda: "test:reviewer")
    monkeypatch.setenv("SIKA_REVIEWER_IDS", "test:reviewer")


@pytest.fixture
def ready(store, authorized):
    """Use a new verified vintage, preserving the original unknown draft fixture."""
    _, engine, tables, _ = store
    with engine.begin() as con:
        con.execute(tables["sources"].update().values(reuse_status="allowed",
                    license_url="https://example.test/synthetic-fixture-license",
                    attribution="Synthetic fixture; no real source figures certified."))
        release = dict(con.execute(sa.select(tables["releases"])).mappings().one())
        con.execute(tables["releases"].insert().values(release | {
            "id": RELEASE_ID, "release_key": "test.verified-vintage",
            "published_on": date(2026, 6, 15), "supersedes_id": 1,
        }))
        observation = dict(con.execute(sa.select(tables["observations"])).mappings().one())
        con.execute(tables["observations"].insert().values(observation | {
            "id": OBSERVATION_ID, "release_id": RELEASE_ID, "revision_status": "final",
        }))
    return store


def checked(ready):
    report = quality.run_quality_checks(ready[0], release_ids=[RELEASE_ID])
    result = report["releases"][0]
    return result, {
        "source_values": "Synthetic PDF fixture values reviewed individually.",
        "revision_marks": "Final revision status verified for this synthetic vintage.",
        "reuse_rights": "Synthetic fixture reuse is explicitly allowed.",
        "verified_observation_ids": [OBSERVATION_ID],
        "warnings": {f["code"]: "Reviewed synthetic fixture limitation."
                     for f in result["findings"] if f["severity"] == "WARN"},
    }


def approve_ready(ready):
    run, evidence = checked(ready)
    assert run["status"] == "passed"
    result = publication.approve(ready[0], RELEASE_ID, run["quality_run_id"],
                                 "Reviewed the complete synthetic vintage.", evidence)
    return result, run, evidence


def approval_id(result):
    identifier = result["review_decision_id"]
    assert isinstance(identifier, int)
    return identifier


def state(store):
    _, engine, tables, _ = store
    with engine.connect() as con:
        return {
            "releases": con.execute(sa.select(tables["releases"].c.id, tables["releases"].c.status)
                                    .order_by(tables["releases"].c.id)).all(),
            "observations": con.execute(sa.select(tables["observations"].c.id, tables["observations"].c.status)
                                        .order_by(tables["observations"].c.id)).all(),
            "decisions": con.execute(sa.select(sa.func.count()).select_from(tables["review_decisions"])).scalar_one(),
            "events": con.execute(sa.select(sa.func.count()).select_from(tables["publication_events"])).scalar_one(),
        }


def test_approved_vintage_publishes_atomically_with_citations(ready):
    target, engine, tables, _ = ready
    assert publication.published_rows(target, RELEASE_ID) == []
    approval, run, _ = approve_ready(ready)
    assert approval["before_snapshot_sha256"] == run["snapshot_sha256"]
    assert approval["after_snapshot_sha256"] != approval["before_snapshot_sha256"]
    assert publication.published_rows(target, RELEASE_ID) == []
    after_review = state(ready)
    assert after_review["releases"] == [(1, "draft"), (RELEASE_ID, "reviewed")]
    assert after_review["observations"] == [(1, "draft"), (OBSERVATION_ID, "reviewed")]
    publication_result = publication.publish(target, RELEASE_ID, approval_id(approval), "Publish reviewed fixture.")
    assert publication_result["before_snapshot_sha256"] == approval["after_snapshot_sha256"]
    assert publication_result["after_snapshot_sha256"] != publication_result["before_snapshot_sha256"]
    result = state(ready)
    assert result["releases"] == [(1, "draft"), (RELEASE_ID, "published")]
    assert result["observations"] == [(1, "draft"), (OBSERVATION_ID, "published")]
    assert result["events"] == result["decisions"] == 2
    rows = publication.published_rows(target, RELEASE_ID)
    assert len(rows) == 1
    assert rows[0]["source_doc"] == DOC
    assert rows[0]["source_page"] == 1
    assert float(rows[0]["value"]) == 0.4
    assert publication.published_rows(target, 1) == []
    with engine.connect() as con:
        reviewers = con.execute(sa.select(tables["review_decisions"].c.reviewer)).scalars().all()
        assert reviewers == ["test:reviewer", "test:reviewer"]


@pytest.mark.parametrize("allowlist", [None, "", "test:another-reviewer"])
def test_operator_authorization_defaults_to_deny(ready, monkeypatch, allowlist):
    run, evidence = checked(ready)
    before = state(ready)
    if allowlist is None:
        monkeypatch.delenv("SIKA_REVIEWER_IDS", raising=False)
    else:
        monkeypatch.setenv("SIKA_REVIEWER_IDS", allowlist)
    with pytest.raises(POLICY_ERRORS):
        publication.approve(ready[0], RELEASE_ID, run["quality_run_id"], "Requested review.", evidence)
    assert state(ready) == before


def test_unknown_rights_date_and_revisions_are_not_waived_by_evidence(store, authorized):
    report = quality.run_quality_checks(store[0], release_ids=[1])["releases"][0]
    assert report["status"] == "passed"
    evidence = {
        "source_values": "Assertion cannot waive policy.", "revision_marks": "Claimed verification.",
        "reuse_rights": "Claimed rights.", "verified_observation_ids": [1],
        "warnings": {f["code"]: "Acknowledged, but not resolved." for f in report["findings"]},
    }
    with pytest.raises(POLICY_ERRORS):
        publication.approve(store[0], 1, report["quality_run_id"], "Review claim.", evidence)
    assert state(store)["decisions"] == state(store)["events"] == 0


@pytest.mark.parametrize("rights", ["unknown", "restricted", "prohibited"])
def test_reuse_policy_blocks_technical_pass(ready, rights):
    with ready[1].begin() as con:
        con.execute(ready[2]["sources"].update().values(reuse_status=rights))
    run, evidence = checked(ready)
    assert run["status"] == "passed"
    with pytest.raises(POLICY_ERRORS):
        publication.approve(ready[0], RELEASE_ID, run["quality_run_id"], "Review claim.", evidence)


@pytest.mark.parametrize("field", ["license_url", "attribution"])
def test_whitespace_does_not_count_as_verified_reuse_metadata(ready, field):
    with ready[1].begin() as con:
        con.execute(ready[2]["sources"].update().values({field: " "}))
    run, evidence = checked(ready)
    assert run["status"] == "passed"
    with pytest.raises(POLICY_ERRORS):
        publication.approve(ready[0], RELEASE_ID, run["quality_run_id"], "Review claim.", evidence)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///private/license.txt", "https://"])
def test_reuse_evidence_requires_a_public_web_url(ready, url):
    with ready[1].begin() as con:
        con.execute(ready[2]["sources"].update().values(license_url=url))
    run, evidence = checked(ready)
    with pytest.raises(POLICY_ERRORS):
        publication.approve(ready[0], RELEASE_ID, run["quality_run_id"], "Review claim.", evidence)


@pytest.mark.parametrize("field", ["source_values", "revision_marks", "reuse_rights"])
def test_review_requires_substantive_evidence_fields(ready, field):
    run, evidence = checked(ready)
    evidence[field] = " "
    with pytest.raises(POLICY_ERRORS):
        publication.approve(ready[0], RELEASE_ID, run["quality_run_id"], "Review claim.", evidence)


@pytest.mark.parametrize("ids", [[], [1], [OBSERVATION_ID, OBSERVATION_ID], [OBSERVATION_ID, 999]])
def test_review_requires_exact_verified_observation_cohort(ready, ids):
    run, evidence = checked(ready)
    evidence["verified_observation_ids"] = ids
    with pytest.raises(POLICY_ERRORS):
        publication.approve(ready[0], RELEASE_ID, run["quality_run_id"], "Review claim.", evidence)


@pytest.mark.parametrize("warnings", [{}, {"small_release": " "}])
def test_every_warning_requires_an_explicit_reason(ready, warnings):
    run, evidence = checked(ready)
    assert "small_release" in evidence["warnings"]
    evidence["warnings"] = warnings
    with pytest.raises(POLICY_ERRORS):
        publication.approve(ready[0], RELEASE_ID, run["quality_run_id"], "Review claim.", evidence)


def test_failed_quality_cannot_authorize_publication(ready):
    with ready[1].begin() as con:
        con.execute(ready[2]["observations"].update().where(ready[2]["observations"].c.id == OBSERVATION_ID)
                    .values(period="2026-13"))
    run, evidence = checked(ready)
    assert run["status"] == "failed"
    with pytest.raises(POLICY_ERRORS):
        publication.approve(ready[0], RELEASE_ID, run["quality_run_id"], "Review claim.", evidence)


@pytest.mark.parametrize("field,value", [("value", 0.5), ("period", "2026-04"),
                                        ("original_label", "Changed label"), ("confidence", 0.9)])
def test_changed_observation_invalidates_the_checked_snapshot(ready, field, value):
    run, evidence = checked(ready)
    with ready[1].begin() as con:
        con.execute(ready[2]["observations"].update().where(ready[2]["observations"].c.id == OBSERVATION_ID)
                    .values({field: value}))
    with pytest.raises(POLICY_ERRORS):
        publication.approve(ready[0], RELEASE_ID, run["quality_run_id"], "Stale review claim.", evidence)


def test_newest_quality_run_prevents_consuming_an_older_pass(ready):
    old, evidence = checked(ready)
    newer, _ = checked(ready)
    assert newer["quality_run_id"] > old["quality_run_id"]
    with pytest.raises(POLICY_ERRORS):
        publication.approve(ready[0], RELEASE_ID, old["quality_run_id"], "Old quality claim.", evidence)


def test_quality_rerun_after_approval_requires_a_new_approval(ready):
    result, _, _ = approve_ready(ready)
    checked(ready)
    with pytest.raises(POLICY_ERRORS):
        publication.publish(ready[0], RELEASE_ID, approval_id(result), "Outdated approval.")


def test_changed_source_bytes_block_approval_and_publication(ready):
    run, evidence = checked(ready)
    original = ready[3].read_bytes()
    ready[3].write_bytes(pdf_bytes(2))
    with pytest.raises(POLICY_ERRORS):
        publication.approve(ready[0], RELEASE_ID, run["quality_run_id"], "Changed source.", evidence)
    ready[3].write_bytes(original)
    result, _, _ = approve_ready(ready)
    ready[3].write_bytes(pdf_bytes(2))
    with pytest.raises(POLICY_ERRORS):
        publication.publish(ready[0], RELEASE_ID, approval_id(result), "Changed source.")


def test_approval_cannot_be_reused_for_a_sibling_release(ready):
    result, _, _ = approve_ready(ready)
    with pytest.raises(POLICY_ERRORS):
        publication.publish(ready[0], 1, approval_id(result), "Copied approval.")
    assert state(ready)["releases"] == [(1, "draft"), (RELEASE_ID, "reviewed")]


@pytest.mark.parametrize("table", ["releases", "observations"])
@pytest.mark.parametrize("status", ["reviewed", "published"])
def test_direct_status_assignment_cannot_bypass_review(ready, table, status):
    before = state(ready)
    with pytest.raises(DBAPIError), ready[1].begin() as con:
        con.execute(ready[2][table].update().where(ready[2][table].c.id == RELEASE_ID).values(status=status))
    with sqlite3.connect(ready[0]) as con, pytest.raises(sqlite3.DatabaseError):
        con.execute(f"UPDATE {table} SET status=? WHERE id=?", (status, RELEASE_ID))
    assert state(ready) == before


@pytest.mark.parametrize("table", ["releases", "observations"])
def test_raw_sql_insertion_cannot_create_published_records(ready, table):
    with sqlite3.connect(ready[0]) as con:
        con.row_factory = sqlite3.Row
        values = dict(con.execute(f"SELECT * FROM {table} WHERE id=?", (RELEASE_ID,)).fetchone())
        values |= {"id": 99, "status": "published"}
        if table == "releases":
            values["release_key"] = "forged.publication"
        else:
            values["period"] = "2026-04"
        keys = ",".join(values)
        placeholders = ",".join("?" for _ in values)
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", tuple(values.values()))


@pytest.mark.parametrize("operation", ["update", "delete", "replace"])
def test_completed_quality_evidence_cannot_be_changed_or_deleted(ready, operation):
    run, _ = checked(ready)
    with sqlite3.connect(ready[0]) as con:
        con.execute("PRAGMA recursive_triggers=OFF")
        if operation == "update":
            statement, params = "UPDATE quality_runs SET report='{}' WHERE id=?", (run["quality_run_id"],)
        elif operation == "delete":
            statement, params = "DELETE FROM quality_runs WHERE id=?", (run["quality_run_id"],)
        else:
            con.row_factory = sqlite3.Row
            values = dict(con.execute("SELECT * FROM quality_runs WHERE id=?", (run["quality_run_id"],)).fetchone())
            values["report"] = "{}"
            statement = f"INSERT OR REPLACE INTO quality_runs ({','.join(values)}) VALUES ({','.join('?' for _ in values)})"
            params = tuple(values.values())
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(statement, params)


@pytest.mark.parametrize("table", ["quality_runs", "publication_events"])
def test_raw_sql_cannot_mint_quality_review_or_publication_evidence(ready, table):
    approve_ready(ready)
    with sqlite3.connect(ready[0]) as con:
        con.row_factory = sqlite3.Row
        values = dict(con.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 1").fetchone())
        values["id"] += 100
        if table == "publication_events":
            review = dict(con.execute("SELECT * FROM review_decisions WHERE id=?",
                                      (values["review_decision_id"],)).fetchone())
            review["id"] += 100
            con.execute(f"INSERT INTO review_decisions ({','.join(review)}) VALUES ({','.join('?' for _ in review)})",
                        tuple(review.values()))
            values["review_decision_id"] = review["id"]
        statement = f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})"
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(statement, tuple(values.values()))


def test_an_unbound_legacy_review_record_cannot_grant_publication(ready):
    approve_ready(ready)
    with ready[1].begin() as con:
        forged = con.execute(ready[2]["review_decisions"].insert().values(
            release_id=RELEASE_ID, reviewer="claimed:reviewer", decision="approve",
            reason="Unbound legacy review is not publication evidence.")).inserted_primary_key[0]
    with pytest.raises(POLICY_ERRORS):
        publication.publish(ready[0], RELEASE_ID, forged, "Copied legacy approval.")


@pytest.mark.parametrize("table", ["review_decisions", "publication_events"])
def test_review_and_publication_history_is_append_only(ready, table):
    approve_ready(ready)
    with sqlite3.connect(ready[0]) as con:
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(f"DELETE FROM {table}")
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(f"UPDATE {table} SET id=id+100")


@pytest.mark.parametrize("field,value", [("value", 9.0), ("period", "2026-04"),
                                        ("original_label", "Changed"), ("revision_status", "provisional")])
def test_reviewed_observation_data_is_frozen(ready, field, value):
    approve_ready(ready)
    with pytest.raises(DBAPIError), ready[1].begin() as con:
        con.execute(ready[2]["observations"].update().where(ready[2]["observations"].c.id == OBSERVATION_ID)
                    .values({field: value}))


def test_replace_with_recursive_triggers_disabled_cannot_overwrite_reviewed_data(ready):
    approve_ready(ready)
    with sqlite3.connect(ready[0]) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA recursive_triggers=OFF")
        values = dict(con.execute("SELECT * FROM observations WHERE id=?", (OBSERVATION_ID,)).fetchone())
        values |= {"status": "draft", "value": 9.0}
        statement = f"INSERT OR REPLACE INTO observations ({','.join(values)}) VALUES ({','.join('?' for _ in values)})"
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(statement, tuple(values.values()))


@pytest.mark.parametrize("action", ["approve", "publish"])
def test_interrupted_publication_transaction_leaves_no_partial_evidence(ready, monkeypatch, action):
    run, evidence = checked(ready)
    existing = None
    if action == "publish":
        existing = publication.approve(ready[0], RELEASE_ID, run["quality_run_id"], "Reviewed fixture.", evidence)
    before = state(ready)
    def interrupted(*args, **kwargs):
        raise RuntimeError("Injected event persistence failure")
    monkeypatch.setattr(publication, "_record_event", interrupted)
    with pytest.raises(RuntimeError, match="Injected"):
        if action == "approve":
            publication.approve(ready[0], RELEASE_ID, run["quality_run_id"], "Reviewed fixture.", evidence)
        else:
            publication.publish(ready[0], RELEASE_ID, approval_id(existing), "Publish fixture.")
    assert state(ready) == before
    # A rolled-back authorized call must not leave a write capability on a pooled connection.
    with pytest.raises(DBAPIError), ready[1].begin() as con:
        con.execute(ready[2]["releases"].update().where(ready[2]["releases"].c.id == RELEASE_ID)
                    .values(status="published"))


def test_new_sibling_vintage_does_not_invalidate_an_approved_scope(ready):
    result, _, _ = approve_ready(ready)
    with ready[1].begin() as con:
        release = dict(con.execute(sa.select(ready[2]["releases"]).where(ready[2]["releases"].c.id == RELEASE_ID))
                       .mappings().one())
        con.execute(ready[2]["releases"].insert().values(release | {
            "id": 3, "release_key": "test.new-sibling", "status": "draft",
        }))
        observation = dict(con.execute(sa.select(ready[2]["observations"]).where(ready[2]["observations"].c.id == OBSERVATION_ID))
                           .mappings().one())
        con.execute(ready[2]["observations"].insert().values(observation | {
            "id": 3, "release_id": 3, "status": "draft",
        }))
    publication.publish(ready[0], RELEASE_ID, approval_id(result), "Publish unchanged reviewed scope.")
    assert len(publication.published_rows(ready[0], RELEASE_ID)) == 1
    assert publication.published_rows(ready[0], 3) == []


def test_latest_failed_quality_withdraws_public_reads_even_after_metadata_restore(ready):
    result, _, _ = approve_ready(ready)
    publication.publish(ready[0], RELEASE_ID, approval_id(result), "Publish fixture.")
    with ready[1].begin() as con:
        con.execute(ready[2]["units"].update().values(symbol="invalid"))
    assert quality.run_quality_checks(ready[0], release_ids=[RELEASE_ID])["status"] == "failed"
    with ready[1].begin() as con:
        con.execute(ready[2]["units"].update().values(symbol="%"))
    with pytest.raises(POLICY_ERRORS):
        publication.published_rows(ready[0], RELEASE_ID)


def test_revoked_publisher_cannot_keep_serving_a_published_cohort(ready, monkeypatch):
    result, _, _ = approve_ready(ready)
    publication.publish(ready[0], RELEASE_ID, approval_id(result), "Publish fixture.")
    monkeypatch.setenv("SIKA_REVIEWER_IDS", "test:new-reviewer")
    with pytest.raises(POLICY_ERRORS):
        publication.published_rows(ready[0], RELEASE_ID)


def test_later_rejection_withdraws_public_reads(ready):
    result, _, _ = approve_ready(ready)
    publication.publish(ready[0], RELEASE_ID, approval_id(result), "Publish fixture.")
    with ready[1].begin() as con:
        con.execute(ready[2]["review_decisions"].insert().values(
            release_id=RELEASE_ID, reviewer="test:reviewer", decision="reject", reason="Review withdrawn."))
    with pytest.raises(POLICY_ERRORS):
        publication.published_rows(ready[0], RELEASE_ID)
