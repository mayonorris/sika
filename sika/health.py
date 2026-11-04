"""Read-only liveness/readiness checks for the deployed application.

Readiness answers "can this instance serve users now?". It never creates,
migrates, stamps or writes a database: SQLite files are opened with
``mode=ro`` and a missing file is reported, not created. Responses contain
check names and closed reason codes only, never paths, URLs or credentials.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = _REPO / "migrations"
LEGACY_TABLES = {
    "observations": {"indicator", "indicator_label", "geography", "period", "value",
                     "unit", "source_doc", "source_page", "confidence"},
    "passages": {"source_doc", "page", "text"},
}
SQLITE_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True)
class Check:
    """One readiness check: ``pass``, ``fail`` or ``skipped`` with a reason code."""

    status: str
    reason: str | None = None
    details: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {"status": self.status}
        if self.reason is not None:
            out["reason"] = self.reason
        out.update(self.details)
        return out


def _open_read_only(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_SECONDS)


def check_legacy_database(path: Path) -> Check:
    """The MVP store must exist, have the served tables/columns and hold rows."""
    if not path.is_file():
        return Check("fail", "database_missing")
    try:
        with closing(_open_read_only(path)) as con:
            for table, required in LEGACY_TABLES.items():
                columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
                if not columns:
                    return Check("fail", "schema_missing", {"table": table})
                if required - columns:
                    return Check("fail", "schema_incomplete", {
                        "table": table, "missing_columns": sorted(required - columns)})
            observations = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    except sqlite3.Error:
        return Check("fail", "database_unreadable")
    if observations == 0:
        return Check("fail", "database_empty", {"observations": 0})
    return Check("pass", details={"observations": observations})


@lru_cache(maxsize=1)
def migration_heads() -> tuple[str, ...]:
    """Head revision(s) shipped with this code, read from the migration scripts."""
    from alembic.script import ScriptDirectory

    return tuple(sorted(ScriptDirectory(str(MIGRATIONS)).get_heads()))


@lru_cache(maxsize=1)
def known_revisions() -> frozenset[str]:
    from alembic.script import ScriptDirectory

    return frozenset(rev.revision for rev in ScriptDirectory(str(MIGRATIONS)).walk_revisions())


def _sqlite_revisions(path: Path) -> tuple[str, ...] | Check:
    if not path.is_file():
        return Check("fail", "database_missing")
    try:
        with closing(_open_read_only(path)) as con:
            exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                 "AND name='alembic_version'").fetchone()
            rows = con.execute("SELECT version_num FROM alembic_version").fetchall() if exists else []
    except sqlite3.Error:
        return Check("fail", "database_unreadable")
    return tuple(sorted(row[0] for row in rows))


def _engine_revisions(url: str) -> tuple[str, ...] | Check:
    """Non-SQLite engines: read the version table without migrating anything."""
    try:
        import sqlalchemy as sa
        from alembic.runtime.migration import MigrationContext

        engine = sa.create_engine(url, pool_pre_ping=True)
    except Exception:
        return Check("fail", "driver_unavailable")
    try:
        with engine.connect() as connection:
            return tuple(sorted(MigrationContext.configure(connection).get_current_heads()))
    except Exception:
        return Check("fail", "database_unreachable")
    finally:
        engine.dispose()


def check_trust_core_schema(url: str | None) -> Check:
    """When a Trust Core database is configured, it must be at the shipped head."""
    if not url:
        return Check("skipped", "not_configured")
    try:
        from sqlalchemy.engine import make_url

        parsed = make_url(url)
        backend = parsed.get_backend_name()
    except Exception:
        return Check("fail", "invalid_url")
    if backend == "sqlite":
        if not parsed.database or parsed.database == ":memory:":
            return Check("fail", "invalid_url", {"backend": backend})
        current = _sqlite_revisions(Path(parsed.database))
    elif backend == "postgresql":
        current = _engine_revisions(url)
    else:
        return Check("fail", "unsupported_backend", {"backend": backend})
    if isinstance(current, Check):
        return Check(current.status, current.reason, {"backend": backend, **current.details})
    try:
        heads, known = migration_heads(), known_revisions()
    except Exception:
        return Check("fail", "migrations_unavailable", {"backend": backend})
    details = {"backend": backend, "current": list(current), "head": list(heads)}
    if len(heads) != 1:
        return Check("fail", "multiple_heads", details)
    if not current:
        return Check("fail", "unversioned", details)
    if set(current) - known:
        return Check("fail", "unknown_revision", details)
    if set(current) != set(heads):
        return Check("fail", "migration_pending", details)
    return Check("pass", details=details)


def readiness(legacy_path: Path, trust_core_url: str | None) -> tuple[bool, dict[str, object]]:
    """Run every check; ready only when none fails (skipped checks do not block)."""
    checks = {
        "legacy_database": check_legacy_database(legacy_path),
        "trust_core_schema": check_trust_core_schema(trust_core_url),
    }
    ready = all(check.status != "fail" for check in checks.values())
    return ready, {"status": "ready" if ready else "not_ready",
                   "checks": {name: check.as_dict() for name, check in checks.items()}}
