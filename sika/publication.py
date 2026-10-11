"""Snapshot-bound local operator review; no web authentication or automatic approval."""

from datetime import datetime, timezone
import csv
import ctypes
import os
from pathlib import Path
import subprocess
from urllib.parse import urlparse

import sqlalchemy as sa

from sika.database import _allow_writes, create_database_engine
from sika import quality
from sika.migrate_inflation import checksum, read_snapshot


class PublicationError(ValueError):
    """Expected refusal: no partial state or audit writes are committed."""


def operator_identity() -> str:
    """Use the operating system identity, never a reviewer supplied in a request."""
    if os.name == "nt":
        buffer = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer)):
            raise PublicationError("Cannot resolve trusted OS identity tool")
        result = subprocess.run([str(Path(buffer.value) / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
                                capture_output=True, text=True, check=True, timeout=10)
        rows = list(csv.reader(result.stdout.strip().splitlines()))
        if len(rows) != 1 or len(rows[0]) != 2 or not rows[0][1].startswith("S-1-"):
            raise PublicationError("Cannot verify OS principal")
        return "windows:" + rows[0][1]
    return f"posix:{os.geteuid()}"


def _reviewers() -> set[str]:
    return {value.strip() for value in os.environ.get("SIKA_REVIEWER_IDS", "").split(",") if value.strip()}


def _operator() -> str:
    principal = operator_identity()
    if principal not in _reviewers():
        raise PublicationError("OS operator is not allowlisted in SIKA_REVIEWER_IDS")
    return principal


def _reason(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise PublicationError("Review reasons must contain 1 to 2000 characters")
    return value.strip()


def _engine(target: Path, legacy: Path | None):
    target = target.resolve(strict=True)
    if legacy is not None and target.samefile(legacy):
        raise PublicationError("Never use the legacy database as a publication target")
    return create_database_engine(sa.URL.create("sqlite", database=str(target)))


def _tables(con: sa.Connection) -> dict:
    names = set(sa.inspect(con).get_table_names())
    if not set(quality.SNAPSHOT_TABLES + ("quality_runs", "publication_events", "review_decisions", "alembic_version")).issubset(names):
        raise PublicationError("An existing migrated Trust Core target is required")
    if con.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() != quality.SCHEMA_VERSION:
        raise PublicationError("Unsupported publication schema revision")
    metadata = sa.MetaData()
    metadata.reflect(con)
    return metadata.tables


def _inspect(con: sa.Connection, tables: dict, legacy: Path | None, root: Path):
    snapshot = quality._read_tables(con, tables)
    originals = read_snapshot(legacy) if legacy is not None else None
    artifacts = {r["id"]: quality._artifact(r, root) for r in snapshot["releases"]}
    findings = quality.examine(snapshot, artifacts, originals)
    return snapshot, artifacts, originals, findings, quality._fingerprint(snapshot, artifacts, originals)


def _scope_hash(snapshot: dict, artifacts: dict, originals: tuple | None, release_id: int) -> str:
    """Release-local content proof: intentional lifecycle transitions are excluded.

    The quality report still binds the full corpus. A sibling's approved transition
    must not invalidate this release. Referenced catalog/source changes do invalidate it.
    """
    selected = {}
    selected["releases"] = [dict(r, status="lifecycle") for r in snapshot["releases"] if r["id"] == release_id]
    selected["observations"] = [dict(r, status="lifecycle") for r in snapshot["observations"] if r["release_id"] == release_id]
    obs_ids = {r["id"] for r in selected["observations"]}
    series_ids = {r["series_id"] for r in selected["observations"]}
    selected["series"] = [r for r in snapshot["series"] if r["id"] in series_ids]
    for name, field in (("concepts", "concept_id"), ("geographies", "geography_id"),
                        ("units", "unit_id"), ("dimensions", "dimensions_id")):
        ids = {r[field] for r in selected["series"]}
        selected[name] = [r for r in snapshot[name] if r["id"] in ids]
    source_ids = {r["source_id"] for r in selected["releases"]}
    selected["sources"] = [r for r in snapshot["sources"] if r["id"] in source_ids]
    selected["passages"] = [r for r in snapshot["passages"] if r["release_id"] == release_id]
    selected["legacy_observation_links"] = [r for r in snapshot["legacy_observation_links"] if r["observation_id"] in obs_ids]
    documents = {r["source_doc"] for r in selected["releases"]}
    for name in ("inflation_migration_rows", "inflation_migration_passages"):
        selected[name] = [r for r in snapshot[name] if r["source_doc"] in documents]
    selected["inflation_migration_runs"] = snapshot["inflation_migration_runs"]
    return quality._fingerprint(selected, {release_id: artifacts[release_id]}, originals)


def _release(snapshot: dict, release_id: int, findings: list):
    matches = [r for r in snapshot["releases"] if r["id"] == release_id]
    if not matches:
        raise PublicationError("Release does not exist")
    release = matches[0]
    rows = [r for r in snapshot["observations"] if r["release_id"] == release_id]
    relevant = [f for f in findings if f.release_id in {None, release_id}]
    if any(f.severity == "FAIL" for f in relevant) or not rows:
        raise PublicationError("Release has technical hard failures")
    source = next(r for r in snapshot["sources"] if r["id"] == release["source_id"])
    license_url, attribution = source["license_url"], source["attribution"]
    if (source["reuse_status"] != "allowed" or not isinstance(license_url, str) or
        urlparse(license_url.strip()).scheme not in {"http", "https"} or not urlparse(license_url.strip()).netloc or
        not isinstance(attribution, str) or not attribution.strip()):
        raise PublicationError("Verified reuse permission, license evidence and attribution are required")
    if release["published_on"] is None or any(r["revision_status"] == "unknown" for r in rows):
        raise PublicationError("Publication date and revision marks must be verified")
    return release, rows, relevant


def _latest_quality(con: sa.Connection, tables: dict, release_id: int):
    table = tables["quality_runs"]
    row = con.execute(sa.select(table).where(table.c.release_id == release_id).order_by(table.c.id.desc()).limit(1)).mappings().first()
    if not row:
        raise PublicationError("No quality run exists for this release")
    report = row["report"]
    if (row["status"] != "passed" or row["hard_failure_count"] != 0 or row["completed_at"] is None or
        row["validator_version"] != quality.VALIDATOR_VERSION or not isinstance(report, dict) or
        report.get("schema_version") != quality.SCHEMA_VERSION or report.get("status") != "passed" or
        report.get("validator_version") != quality.VALIDATOR_VERSION or report.get("release_id") != release_id or
        report.get("hard_failure_count") != 0 or report.get("warning_count") != row["warning_count"]):
        raise PublicationError("Latest quality evidence is incomplete, failed or obsolete")
    return row


def _evidence(evidence: dict, rows: list, findings: list) -> dict:
    if not isinstance(evidence, dict):
        raise PublicationError("Structured review evidence is required")
    clean = {field: _reason(evidence.get(field)) for field in ("source_values", "revision_marks", "reuse_rights")}
    ids = evidence.get("verified_observation_ids")
    if not isinstance(ids, list) or any(type(i) is not int for i in ids) or sorted(ids) != sorted(r["id"] for r in rows):
        raise PublicationError("Source-value review must cover every observation exactly once")
    warnings = evidence.get("warnings")
    expected = {f.code for f in findings if f.severity == "WARN"}
    if not isinstance(warnings, dict) or set(warnings) != expected:
        raise PublicationError("Every current warning needs an explicit review reason")
    clean["warnings"] = {code: _reason(warnings[code]) for code in sorted(expected)}
    clean["verified_observation_ids"] = sorted(ids)
    return clean


def _unchanged(snapshot: dict, artifacts: dict, originals: tuple | None, legacy: Path | None, root: Path) -> None:
    if artifacts != {r["id"]: quality._artifact(r, root) for r in snapshot["releases"]}:
        raise PublicationError("Source artifact changed during review")
    if originals and checksum(legacy) != originals[0].removeprefix("sha256:"):
        raise PublicationError("Legacy snapshot changed during review")


def _record_event(con, tables, *, release_id, quality_id, action, reason, reviewer, evidence, before, after):
    decision = "approve" if action == "approve" else "publish"
    review_id = con.execute(tables["review_decisions"].insert().values(
        release_id=release_id, reviewer=reviewer, decision=decision, reason=reason)).inserted_primary_key[0]
    with _allow_writes(con, ("publication_events", "insert", -1)):
        event_id = con.execute(tables["publication_events"].insert().values(
            release_id=release_id, quality_run_id=quality_id, review_decision_id=review_id, action=action,
            before_snapshot_sha256=before, after_snapshot_sha256=after, evidence=evidence,
            created_at=datetime.now(timezone.utc))).inserted_primary_key[0]
    return dict(event_id=event_id, review_decision_id=review_id, release_id=release_id, action=action,
                before_snapshot_sha256=before, after_snapshot_sha256=after)


def _transition(con, tables, release_id: int, rows: list, state: str) -> None:
    permissions = [("releases", "transition", release_id)] + [("observations", "transition", r["id"]) for r in rows]
    with _allow_writes(con, *permissions):
        con.execute(tables["observations"].update().where(tables["observations"].c.release_id == release_id).values(status=state))
        con.execute(tables["releases"].update().where(tables["releases"].c.id == release_id).values(status=state))


def approve(target: Path, release_id: int, quality_run_id: int, reason: str, evidence: dict, *,
            legacy: Path | None = None, source_root: Path | None = None) -> dict:
    """Record source review and atomically move an entire valid cohort to reviewed."""
    reviewer, reason = _operator(), _reason(reason)
    engine = _engine(target, legacy)
    root = source_root or target.resolve().parent / "trust_core_sources"
    try:
        with engine.begin() as con:
            tables = _tables(con)
            con.execute(tables["quality_runs"].update().where(sa.false()).values(status="running"))
            snapshot, artifacts, originals, findings, before = _inspect(con, tables, legacy, root)
            release, rows, relevant = _release(snapshot, release_id, findings)
            if release["status"] not in {"draft", "reviewed"} or any(r["status"] not in {"draft", "reviewed"} for r in rows):
                raise PublicationError("Only draft/reviewed cohorts can be approved")
            run = _latest_quality(con, tables, release_id)
            if run["id"] != quality_run_id or run["report"].get("snapshot_sha256") != before:
                raise PublicationError("Quality evidence does not match the current snapshot")
            clean = _evidence(evidence, rows, relevant)
            clean["scope_sha256"] = _scope_hash(snapshot, artifacts, originals, release_id)
            _unchanged(snapshot, artifacts, originals, legacy, root)
            _transition(con, tables, release_id, rows, "reviewed")
            after = quality._fingerprint(quality._read_tables(con, tables), artifacts, originals)
            result = _record_event(con, tables, release_id=release_id, quality_id=run["id"], action="approve",
                                   reason=reason, reviewer=reviewer, evidence=clean, before=before, after=after)
            _unchanged(snapshot, artifacts, originals, legacy, root)
            return result
    finally:
        engine.dispose()


def _approval(con, tables, release_id: int, approval_id: int, rows: list):
    events, reviews = tables["publication_events"], tables["review_decisions"]
    event = con.execute(sa.select(events).where(events.c.review_decision_id == approval_id)).mappings().first()
    review = con.execute(sa.select(reviews).where(reviews.c.id == approval_id)).mappings().first()
    if not event or not review or event["release_id"] != release_id or event["action"] != "approve" or review["decision"] != "approve":
        raise PublicationError("Approval does not belong to this release")
    if review["reviewer"] not in _reviewers():
        raise PublicationError("Approving operator is no longer authorized")
    later = con.execute(sa.select(reviews.c.id).where(reviews.c.id > approval_id,
        sa.or_(reviews.c.release_id == release_id, reviews.c.observation_id.in_([r["id"] for r in rows]))).limit(1)).first()
    if later:
        raise PublicationError("A later review decision supersedes this approval")
    return event


def publish(target: Path, release_id: int, approval_id: int, reason: str, *,
            legacy: Path | None = None, source_root: Path | None = None) -> dict:
    """Publish only a still-current, explicitly reviewed cohort; never accept a PASS alone."""
    reviewer, reason = _operator(), _reason(reason)
    engine = _engine(target, legacy)
    root = source_root or target.resolve().parent / "trust_core_sources"
    try:
        with engine.begin() as con:
            tables = _tables(con)
            con.execute(tables["quality_runs"].update().where(sa.false()).values(status="running"))
            snapshot, artifacts, originals, findings, before = _inspect(con, tables, legacy, root)
            release, rows, relevant = _release(snapshot, release_id, findings)
            if release["status"] != "reviewed" or any(r["status"] != "reviewed" for r in rows):
                raise PublicationError("Every observation must belong to the reviewed cohort")
            event = _approval(con, tables, release_id, approval_id, rows)
            run = _latest_quality(con, tables, release_id)
            if run["id"] != event["quality_run_id"] or event["evidence"].get("scope_sha256") != _scope_hash(snapshot, artifacts, originals, release_id):
                raise PublicationError("Quality run or reviewed source content changed; review again")
            _evidence(event["evidence"], rows, relevant)
            _unchanged(snapshot, artifacts, originals, legacy, root)
            _transition(con, tables, release_id, rows, "published")
            after = quality._fingerprint(quality._read_tables(con, tables), artifacts, originals)
            result = _record_event(con, tables, release_id=release_id, quality_id=run["id"], action="publish",
                                   reason=reason, reviewer=reviewer, evidence=event["evidence"], before=before, after=after)
            _unchanged(snapshot, artifacts, originals, legacy, root)
            return result
    finally:
        engine.dispose()


def published_rows(target: Path, release_id: int, *, legacy: Path | None = None,
                   source_root: Path | None = None) -> list[dict]:
    """Future query boundary: fail closed unless published facts retain their proof."""
    engine = _engine(target, legacy)
    root = source_root or target.resolve().parent / "trust_core_sources"
    try:
        with engine.connect() as con:
            tables = _tables(con)
            snapshot, artifacts, originals, findings, _ = _inspect(con, tables, legacy, root)
            candidate = next((r for r in snapshot["releases"] if r["id"] == release_id), None)
            if candidate is None or candidate["status"] != "published":
                return []
            release, rows, relevant = _release(snapshot, release_id, findings)
            if any(r["status"] != "published" for r in rows):
                raise PublicationError("Published cohort is incomplete")
            events = tables["publication_events"]
            event = con.execute(sa.select(events).where(events.c.release_id == release_id).order_by(events.c.id.desc()).limit(1)).mappings().first()
            if not event or event["action"] != "publish" or event["evidence"].get("scope_sha256") != _scope_hash(snapshot, artifacts, originals, release_id):
                raise PublicationError("Published content no longer matches its proof")
            _latest_quality(con, tables, release_id)
            reviews = tables["review_decisions"]
            decision = con.execute(sa.select(reviews).where(reviews.c.id == event["review_decision_id"])).mappings().one()
            if decision["reviewer"] not in _reviewers():
                raise PublicationError("Publishing operator is no longer authorized")
            later = con.execute(sa.select(reviews.c.id).where(reviews.c.id > decision["id"],
                sa.or_(reviews.c.release_id == release_id, reviews.c.observation_id.in_([r["id"] for r in rows]))).limit(1)).first()
            if later:
                raise PublicationError("A later review decision invalidates public visibility")
            _evidence(event["evidence"], rows, relevant)
            _unchanged(snapshot, artifacts, originals, legacy, root)
            return [r | {"source_doc": release["source_doc"]} for r in rows]
    finally:
        engine.dispose()
