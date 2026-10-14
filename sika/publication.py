"""Snapshot-bound local operator review; no web authentication or automatic approval."""

from datetime import datetime, timezone
from contextlib import closing, contextmanager
from dataclasses import dataclass, fields
import csv
import ctypes
import os
import math
from pathlib import Path
import subprocess
import sqlite3
import time
from collections.abc import Callable, Iterator
from urllib.parse import urlparse
from urllib.request import url2pathname

import sqlalchemy as sa

from sika.database import _allow_writes, create_database_engine
from sika import quality
from sika.migrate_inflation import checksum, read_snapshot
from sika.inflation_mapping import in_scope


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


def _inspect(con: sa.Connection, tables: dict, legacy: Path | None, root: Path,
             guard: Callable[[], None] | None = None,
             legacy_reader: Callable[[Path], tuple] | None = None):
    check = guard or (lambda: None)
    check()
    snapshot = quality._read_tables(con, tables)
    check()
    originals = (legacy_reader or read_snapshot)(legacy) if legacy is not None else None
    check()
    artifacts = {}
    for release in snapshot["releases"]:
        check()
        artifacts[release["id"]] = quality._artifact(release, root)
        check()
    findings = quality.examine(snapshot, artifacts, originals)
    check()
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


def _unchanged(snapshot: dict, artifacts: dict, originals: tuple | None, legacy: Path | None, root: Path,
               guard: Callable[[], None] | None = None) -> None:
    check = guard or (lambda: None)
    current = {}
    for release in snapshot["releases"]:
        check()
        current[release["id"]] = quality._artifact(release, root)
        check()
    if artifacts != current:
        raise PublicationError("Source artifact changed during review")
    check()
    if originals and checksum(legacy) != originals[0].removeprefix("sha256:"):
        raise PublicationError("Legacy snapshot changed during review")
    check()


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


def _retained_rows(con, tables, snapshot, artifacts, originals, findings, release_id):
    """Apply the original retained-proof policy to one held snapshot."""
    candidate = next((r for r in snapshot["releases"] if r["id"] == release_id), None)
    if candidate is None or candidate["status"] != "published":
        return [], None
    release, rows, relevant = _release(snapshot, release_id, findings)
    if any(r["status"] != "published" for r in rows):
        raise PublicationError("Published cohort is incomplete")
    events = tables["publication_events"]
    event = con.execute(sa.select(events).where(events.c.release_id == release_id)
                       .order_by(events.c.id.desc()).limit(1)).mappings().first()
    if (not event or event["action"] != "publish" or
        event["evidence"].get("scope_sha256") != _scope_hash(snapshot, artifacts, originals, release_id)):
        raise PublicationError("Published content no longer matches its proof")
    _latest_quality(con, tables, release_id)
    reviews = tables["review_decisions"]
    decision = con.execute(sa.select(reviews).where(reviews.c.id == event["review_decision_id"])).mappings().one()
    if decision["reviewer"] not in _reviewers():
        raise PublicationError("Publishing operator is no longer authorized")
    later = con.execute(sa.select(reviews.c.id).where(reviews.c.id > decision["id"],
        sa.or_(reviews.c.release_id == release_id, reviews.c.observation_id.in_([r["id"] for r in rows])))
        .limit(1)).first()
    if later:
        raise PublicationError("A later review decision invalidates public visibility")
    _evidence(event["evidence"], rows, relevant)
    return [r | {"source_doc": release["source_doc"]} for r in rows], event["id"]


def published_rows(target: Path, release_id: int, *, legacy: Path | None = None,
                   source_root: Path | None = None, _session: "ReadSession | None" = None) -> list[dict]:
    """Fail closed unless published facts retain fresh snapshot-bound proof.

    The private session argument is solely for the internal query adapter. It
    preserves one transaction across catalog resolution and multiple cohorts.
    """
    if _session is not None:
        if not isinstance(_session, ReadSession):
            raise PublicationError("Invalid internal publication read session")
        _session._assert_target(target, legacy, source_root)
        rows, _ = _session._retained(release_id)
        return rows
    engine = _engine(target, legacy)
    root = source_root or target.resolve().parent / "trust_core_sources"
    try:
        with engine.connect() as con:
            tables = _tables(con)
            snapshot, artifacts, originals, findings, _ = _inspect(con, tables, legacy, root)
            rows, _ = _retained_rows(con, tables, snapshot, artifacts, originals, findings, release_id)
            _unchanged(snapshot, artifacts, originals, legacy, root)
            return rows
    finally:
        engine.dispose()


class ReadLimitError(PublicationError):
    """Sanitized refusal during bounded publication reads."""

    def __init__(self, code: str) -> None:
        super().__init__("Publication read could not complete within its safety limits.")
        self.code = code


@dataclass(frozen=True)
class ReadLimits:
    """Server-owned ceilings; callers may only select smaller positive limits."""

    max_rows: int = 50000
    max_bytes: int = 16 * 1024 * 1024
    max_cell_bytes: int = 65536
    max_artifact_bytes: int = 32 * 1024 * 1024
    max_total_artifact_bytes: int = 64 * 1024 * 1024
    max_legacy_bytes: int = 64 * 1024 * 1024
    max_tables: int = 32
    sql_timeout_ms: int = 5000
    lock_timeout_ms: int = 250
    sql_steps: int = 5000000

    def __post_init__(self) -> None:
        ceilings = {
            "max_rows": 50000, "max_bytes": 16 * 1024 * 1024,
            "max_cell_bytes": 65536, "max_artifact_bytes": 32 * 1024 * 1024,
            "max_total_artifact_bytes": 64 * 1024 * 1024,
            "max_legacy_bytes": 64 * 1024 * 1024, "max_tables": 32,
            "sql_timeout_ms": 5000, "lock_timeout_ms": 250, "sql_steps": 5000000,
        }
        for name, ceiling in ceilings.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError("Read limits must be bounded positive integers")


class _ReadBudget:
    def __init__(self, limits: ReadLimits, deadline: float) -> None:
        self.limits, self.deadline, self.steps = limits, deadline, 0
        self.refusal: str | None = None

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            self.refusal = "query_timeout"
            raise ReadLimitError(self.refusal)
        if self.steps >= self.limits.sql_steps:
            self.refusal = "resource_limit"
            raise ReadLimitError(self.refusal)

    def progress(self) -> int:
        self.steps += 1000
        try:
            self.check()
        except ReadLimitError:
            return 1
        return 0


def _checkpointed_legacy(path: Path, limits: ReadLimits, budget: _ReadBudget) -> None:
    budget.check()
    if path.stat().st_size > limits.max_legacy_bytes:
        raise ReadLimitError("resource_limit")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise PublicationError("Legacy database requires a checkpointed immutable snapshot")


def _bounded_legacy_snapshot(path: Path, limits: ReadLimits, budget: _ReadBudget) -> tuple:
    """Preserve read_snapshot semantics with independent bounded legacy inputs."""
    path = path.resolve(strict=True)
    _checkpointed_legacy(path, limits, budget)
    before = checksum(path)
    budget.check()
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                    timeout=limits.lock_timeout_ms / 1000)) as con:
            con.row_factory = sqlite3.Row
            con.set_progress_handler(budget.progress, 1000)
            try:
                con.execute("PRAGMA query_only=ON")
                con.execute("BEGIN")
                kinds = {row["name"]: row["type"] for row in con.execute(
                    "SELECT name,type FROM sqlite_master WHERE name IN (?,?)", ("observations", "passages"))}
                if kinds != {"observations": "table", "passages": "table"}:
                    raise PublicationError("Legacy observations and passages must be physical tables")
                row_count = byte_count = 0
                for name in ("observations", "passages"):
                    budget.check()
                    columns = [row["name"] for row in con.execute(f'PRAGMA table_info("{name}")')]
                    lengths = [f'COALESCE(length(CAST("{column.replace(chr(34), chr(34) * 2)}" AS BLOB)),0)'
                               for column in columns]
                    total = "+".join(lengths) or "0"
                    cell = "max(" + ",".join(lengths) + ")" if len(lengths) > 1 else lengths[0]
                    rows, size, largest = con.execute(
                        f'SELECT COUNT(*),COALESCE(SUM({total}),0),COALESCE(MAX({cell}),0) FROM "{name}"').fetchone()
                    row_count += rows
                    byte_count += size
                    if (row_count > limits.max_rows or byte_count > limits.max_bytes or
                        largest > limits.max_cell_bytes):
                        raise ReadLimitError("resource_limit")
                observations = []
                for row in con.execute("SELECT * FROM observations ORDER BY id"):
                    budget.check()
                    item = dict(row)
                    if in_scope(item):
                        observations.append(item)
                documents = {row["source_doc"] for row in observations}
                passages = []
                for row in con.execute("SELECT * FROM passages ORDER BY id"):
                    budget.check()
                    if row["source_doc"] in documents:
                        passages.append(dict(row))
            finally:
                con.set_progress_handler(None, 0)
                con.rollback()
    except sqlite3.Error:
        raise ReadLimitError(budget.refusal or "database_unavailable") from None
    _checkpointed_legacy(path, limits, budget)
    if checksum(path) != before:
        raise PublicationError("Legacy snapshot changed during the read")
    budget.check()
    return f"sha256:{before}", observations, passages


def _read_preflight(con: sa.Connection, limits: ReadLimits, budget: _ReadBudget,
                    legacy: Path | None, root: Path) -> None:
    """Reject oversized input before reflection, JSON decoding or source parsing."""
    budget.check()
    count = con.exec_driver_sql("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").scalar_one()
    if count > limits.max_tables:
        raise ReadLimitError("resource_limit")
    names = set(sa.inspect(con).get_table_names())
    selected = quality.SNAPSHOT_TABLES + ("quality_runs", "publication_events", "review_decisions", "alembic_version")
    if not set(selected).issubset(names):
        raise PublicationError("An existing migrated Trust Core target is required")
    row_count = byte_count = 0
    preparer = con.dialect.identifier_preparer
    for name in selected:
        budget.check()
        columns = sa.inspect(con).get_columns(name)
        lengths = [f'COALESCE(length(CAST({preparer.quote(c["name"])} AS BLOB)),0)' for c in columns]
        total = "+".join(lengths) or "0"
        cell = "max(" + ",".join(lengths) + ")" if len(lengths) > 1 else lengths[0]
        sql = (f"SELECT COUNT(*), COALESCE(SUM({total}),0), COALESCE(MAX({cell}),0) "
               f"FROM {preparer.quote(name)}")
        rows, size, largest = con.exec_driver_sql(sql).one()
        row_count += rows
        byte_count += size
        if row_count > limits.max_rows or byte_count > limits.max_bytes or largest > limits.max_cell_bytes:
            raise ReadLimitError("resource_limit")
    if legacy is not None and legacy.stat().st_size > limits.max_legacy_bytes:
        raise ReadLimitError("resource_limit")
    total_artifacts = 0
    for uri_text, in con.exec_driver_sql("SELECT storage_uri FROM releases"):
        budget.check()
        uri = urlparse(uri_text)
        if uri.scheme != "file" or uri.netloc not in {"", "localhost"} or uri.query or uri.fragment:
            continue  # The existing technical checker rejects unsupported artifacts.
        path = Path(url2pathname(uri.path)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            continue
        size = path.stat().st_size
        total_artifacts += size
        if size > limits.max_artifact_bytes or total_artifacts > limits.max_total_artifact_bytes:
            raise ReadLimitError("resource_limit")
    budget.check()


class ReadSession:
    """Internal active snapshot; not a serializable or request-supplied capability."""

    def __init__(self, con, tables, snapshot, artifacts, originals, findings, fingerprint,
                 target, legacy, root, budget) -> None:
        self._con, self._tables = con, tables
        self._snapshot, self._artifacts, self._originals, self._findings = snapshot, artifacts, originals, findings
        self._fingerprint = fingerprint
        self._target, self._legacy, self._root, self._budget = target, legacy, root, budget
        self._active = True
        self._publication_ids: dict[int, int | None] = {}

    def check_budget(self) -> None:
        if not self._active:
            raise PublicationError("Publication read session is no longer active")
        self._budget.check()

    @property
    def deadline(self) -> float:
        self.check_budget()
        return self._budget.deadline

    @property
    def snapshot_sha256(self) -> str:
        self.check_budget()
        return self._fingerprint

    def _assert_target(self, target, legacy, root) -> None:
        self.check_budget()
        actual_target = Path(target).resolve(strict=True)
        actual_legacy = Path(legacy).resolve(strict=True) if legacy is not None else None
        actual_root = (Path(root) if root is not None else actual_target.parent / "trust_core_sources").resolve()
        if (actual_target, actual_legacy, actual_root) != (self._target, self._legacy, self._root):
            raise PublicationError("Publication read session belongs to another target")

    def _retained(self, release_id: int):
        self.check_budget()
        if type(release_id) is not int or release_id < 1:
            raise PublicationError("Invalid release identifier")
        rows, event_id = _retained_rows(self._con, self._tables, self._snapshot, self._artifacts,
                                       self._originals, self._findings, release_id)
        self._publication_ids[release_id] = event_id
        self.check_budget()
        return rows, event_id

    def catalog_records(self) -> tuple[dict, ...]:
        self.check_budget()
        releases = {r["id"] for r in self._snapshot["releases"] if r["status"] == "published"}
        series_ids = {r["series_id"] for r in self._snapshot["observations"] if r["release_id"] in releases}
        catalogs = {name: {r["id"]: r for r in self._snapshot[name]}
                    for name in ("concepts", "geographies", "units", "dimensions")}
        records = []
        for series in self._snapshot["series"]:
            if series["id"] not in series_ids:
                continue
            try:
                concept = catalogs["concepts"][series["concept_id"]]
                geography = catalogs["geographies"][series["geography_id"]]
                unit = catalogs["units"][series["unit_id"]]
                dims = catalogs["dimensions"][series["dimensions_id"]]
            except KeyError:
                raise PublicationError("Published catalog has an incomplete identity") from None
            record = dict(series_key=series["series_key"], concept=concept["code"], geography=geography["code"],
                          frequency=series["frequency"], unit=unit["code"],
                          **{name: dims[name] for name in quality.DIMENSIONS})
            for name, item in (("concept", concept), ("geography", geography)):
                record[name + "_aliases"] = tuple(dict.fromkeys(item[field] for field in ("label_fr", "label_en")
                                                                 if isinstance(item.get(field), str) and item[field]))
            records.append(record)
        self.check_budget()
        return tuple(sorted(records, key=lambda r: r["series_key"]))

    def release_ids(self, series_key: str) -> tuple[int, ...]:
        self.check_budget()
        ids = {r["id"] for r in self._snapshot["series"] if r["series_key"] == series_key}
        releases = {r["id"] for r in self._snapshot["releases"] if r["status"] == "published"}
        return tuple(sorted({r["release_id"] for r in self._snapshot["observations"]
                             if r["series_id"] in ids and r["release_id"] in releases}))

    def projected_rows(self, release_id: int) -> list[dict]:
        self.check_budget()
        rows = published_rows(self._target, release_id, legacy=self._legacy, source_root=self._root, _session=self)
        if not rows:
            return []
        release = next(r for r in self._snapshot["releases"] if r["id"] == release_id)
        source = next(r for r in self._snapshot["sources"] if r["id"] == release["source_id"])
        series = {r["id"]: r for r in self._snapshot["series"]}
        units = {r["id"]: r for r in self._snapshot["units"]}
        result = []
        for row in rows:
            self.check_budget()
            item = series[row["series_id"]]
            result.append(dict(
                observation_id=row["id"], series_key=item["series_key"], period=row["period"], value=row["value"],
                unit=units[item["unit_id"]]["code"], release_id=release_id, source_doc=release["source_doc"],
                source_page=row["source_page"], source_url=release["source_url"], source_locator=row["source_locator"],
                revision_status=row["revision_status"], publisher=release["publisher"], attribution=source["attribution"],
                license_url=source["license_url"], release_key=release["release_key"],
                published_on=release["published_on"].isoformat(), content_sha256=release["content_sha256"],
                publication_event_id=self._publication_ids[release_id],
            ))
        return result


@contextmanager
def published_read(target: Path, *, legacy: Path | None = None, source_root: Path | None = None,
                   limits: ReadLimits | None = None, deadline: float | None = None) -> Iterator[ReadSession]:
    """Hold one writer-reserved, query-only SQLite snapshot with fresh proofs.

    Deadlines are checked between artifact operations and bound SQL execution;
    they are not a hard operating-system timeout for the local PDF parser.
    """
    if limits is None:
        limits = ReadLimits()
    elif not isinstance(limits, ReadLimits):
        raise ValueError("Expected server-owned read limits")
    else:
        limits = ReadLimits(**{field.name: getattr(limits, field.name) for field in fields(ReadLimits)})
    end = time.monotonic() + limits.sql_timeout_ms / 1000
    if deadline is not None:
        if type(deadline) not in {int, float} or not math.isfinite(deadline):
            raise ValueError("Expected a finite internal deadline")
        end = min(end, deadline)
    budget = _ReadBudget(limits, end)
    budget.check()
    target = Path(target).resolve(strict=True)
    legacy = Path(legacy).resolve(strict=True) if legacy is not None else None
    if legacy is not None and target.samefile(legacy):
        raise PublicationError("Never use the legacy database as a publication target")
    root = (Path(source_root) if source_root is not None else target.parent / "trust_core_sources").resolve()
    initial_reviewers = _reviewers()
    engine = create_database_engine(sa.URL.create("sqlite", database=str(target)), sqlite_begin="immediate")
    session = None
    try:
        with engine.connect() as con:
            raw = con.connection.driver_connection
            raw.execute(f"PRAGMA busy_timeout={limits.lock_timeout_ms}")
            raw.set_progress_handler(budget.progress, 1000)
            transaction = None
            try:
                transaction = con.begin()  # BEGIN IMMEDIATE must precede query_only.
                raw.execute("PRAGMA query_only=ON")
                _read_preflight(con, limits, budget, legacy, root)
                tables = _tables(con)
                snapshot, artifacts, originals, findings, fingerprint = _inspect(
                    con, tables, legacy, root, budget.check,
                    lambda path: _bounded_legacy_snapshot(path, limits, budget))
                session = ReadSession(con, tables, snapshot, artifacts, originals, findings, fingerprint,
                                      target, legacy, root, budget)
                yield session
                session.check_budget()
                _read_preflight(con, limits, budget, legacy, root)
                _unchanged(snapshot, artifacts, originals, legacy, root, budget.check)
                if legacy is not None:
                    _checkpointed_legacy(legacy, limits, budget)
                if _reviewers() != initial_reviewers:
                    raise PublicationError("Reviewer authorization changed during the read")
                budget.check()
            finally:
                if session is not None:
                    session._active = False
                raw.set_progress_handler(None, 0)
                if transaction is not None and transaction.is_active:
                    transaction.rollback()
                raw.execute("PRAGMA query_only=OFF")
    except sa.exc.DBAPIError:
        raise ReadLimitError(budget.refusal or "database_unavailable") from None
    finally:
        engine.dispose()
