"""Proof-bound query service; enabled only in isolated synthetic fixture APIs.

Accept intent only, compile internally, and execute on an ephemeral relation made
exclusively from fresh protected publication reads. No model, raw SQL or cached
proof can be supplied by a request. Production identity/PostgreSQL are deferred.
"""

from collections import Counter
from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3
import time
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.exc import SQLAlchemyError

from sika import publication, quality
from sika.query import CatalogEntry, PreparedQuery, QueryError, QueryIntent, SeriesCatalog, compile_query, parse_intent

MVP_DATABASE = Path(__file__).parent.parent / "data" / "processed" / "sika.db"
_RELATION_COLUMNS = (
    "observation_id", "series_key", "period", "value", "unit", "release_id",
    "source_doc", "source_page", "source_url", "source_locator", "revision_status",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True,
                              revalidate_instances="always", arbitrary_types_allowed=True)


class ExecutionLimits(_StrictModel):
    """Server policy, never accepted as fields inside the user query intent.

    A trusted caller may tighten but not raise hard ceilings. Revalidation also
    rejects forged/copied instances of this model or its nested read policy.
    """

    max_releases: Annotated[int, Field(ge=1, le=16)] = 16
    max_authorized_rows: Annotated[int, Field(ge=1, le=10000)] = 10000
    max_result_bytes: Annotated[int, Field(ge=1024, le=262144)] = 262144
    sql_steps: Annotated[int, Field(ge=1, le=1000000)] = 1000000
    timeout_ms: Annotated[int, Field(ge=1, le=5000)] = 5000
    read_limits: publication.ReadLimits = Field(default_factory=publication.ReadLimits)

    @field_validator("read_limits")
    @classmethod
    def checked_read_limits(cls, value: publication.ReadLimits) -> publication.ReadLimits:
        if not isinstance(value, publication.ReadLimits):
            raise ValueError("Expected server-owned read limits")
        try:
            return publication.ReadLimits(**{item.name: getattr(value, item.name)
                                             for item in fields(publication.ReadLimits)})
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Invalid read limits") from None


class Citation(_StrictModel):
    source_doc: Annotated[str, Field(min_length=1)]
    source_page: Annotated[int, Field(ge=1)]
    source_url: Annotated[str, Field(min_length=1)]
    source_locator: str | None
    publisher: Annotated[str, Field(min_length=1)]
    attribution: Annotated[str, Field(min_length=1)]
    license_url: Annotated[str, Field(min_length=1)]
    release_key: Annotated[str, Field(min_length=1)]
    published_on: Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
    content_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    publication_event_id: Annotated[int, Field(ge=1)]

    @field_validator("source_url", "license_url")
    @classmethod
    def public_web_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Citation needs a public web URL")
        return value


class CitedObservation(_StrictModel):
    observation_id: Annotated[int, Field(ge=1)]
    series_key: str
    period: str
    value: Annotated[Decimal, Field(max_digits=28, decimal_places=10, allow_inf_nan=False)]
    unit: Literal["percent", "index"]
    release_id: Annotated[int, Field(ge=1)]
    revision_status: Literal["provisional", "revised", "final"]
    citation: Citation


class QueryResult(_StrictModel):
    """Complete means not row-truncated, not complete historical source coverage.

    Decimal values serialize as JSON strings; no floating-point conversion or
    unqualified chart payload is introduced in this execution brick.
    """

    version: Literal[1] = 1
    status: Literal["ok", "empty", "truncated"]
    series: CatalogEntry
    period_start: str
    period_end: str
    rows: tuple[CitedObservation, ...]
    row_count: int
    has_more: bool
    complete: bool
    warnings: tuple[str, ...]
    snapshot_sha256: str
    as_of_utc: str


def _policy(limits: ExecutionLimits | None) -> ExecutionLimits:
    try:
        return ExecutionLimits.model_validate(limits if limits is not None else ExecutionLimits())
    except (ValidationError, ValueError, TypeError, AttributeError):
        raise QueryError("invalid_execution_limits", "The server execution policy is invalid.") from None


def _target(target: Path, legacy: Path | None) -> Path:
    if not isinstance(target, Path) or (legacy is not None and not isinstance(legacy, Path)):
        raise QueryError("database_unavailable", "An explicit local database target is required.")
    resolved = target.resolve(strict=True)
    if ((MVP_DATABASE.is_file() and resolved.samefile(MVP_DATABASE)) or
            (legacy is not None and resolved.samefile(legacy))):
        raise QueryError("database_unavailable", "The legacy database is not a query execution target.")
    return resolved


def _cited(row: dict, proved: dict) -> CitedObservation:
    # The SQL output has only an exact observation ID; citation metadata is taken
    # from that same proved row, not from another query or a filename guess.
    if any(row[name] != proved[name] for name in _RELATION_COLUMNS):
        raise QueryError("publication_unavailable", "The selected row no longer matches its proved provenance.")
    value = quality.numeric(row["value"])
    if value is None:
        raise QueryError("publication_unavailable", "The proved numeric value is invalid.")
    citation = Citation.model_validate({name: proved[name] for name in Citation.model_fields})
    return CitedObservation(
        observation_id=row["observation_id"], series_key=row["series_key"], period=row["period"],
        value=value, unit=row["unit"], release_id=row["release_id"], revision_status=row["revision_status"],
        citation=citation,
    )


def _execute_memory(plan: PreparedQuery, proved_rows: list[dict], policy: ExecutionLimits,
                    read: publication.ReadSession) -> list[dict]:
    """Execute only the internally compiled template on an isolated memory DB."""
    deadline = read.deadline
    memory = sqlite3.connect(":memory:")
    memory.row_factory = sqlite3.Row
    steps = 0
    interrupted: str | None = None

    def progress() -> int:
        nonlocal steps, interrupted
        steps += 100
        if time.monotonic() >= deadline:
            interrupted = "query_timeout"
        elif steps >= policy.sql_steps:
            interrupted = "resource_limit"
        return int(interrupted is not None)

    try:
        memory.set_progress_handler(progress, 100)
        memory.execute("""CREATE TABLE authorized_observations (
            observation_id INTEGER PRIMARY KEY, series_key TEXT NOT NULL,
            period TEXT NOT NULL, value TEXT NOT NULL, unit TEXT NOT NULL,
            release_id INTEGER NOT NULL, source_doc TEXT NOT NULL,
            source_page INTEGER NOT NULL, source_url TEXT NOT NULL,
            source_locator TEXT, revision_status TEXT NOT NULL,
            UNIQUE (series_key, period, release_id)
        )""")
        placeholders = ",".join("?" for _ in _RELATION_COLUMNS)
        memory.executemany(f"INSERT INTO authorized_observations VALUES ({placeholders})",
                           [tuple(row[name] for name in _RELATION_COLUMNS) for row in proved_rows])
        read.check_budget()
        memory.execute("PRAGMA query_only=ON")
        return [dict(row) for row in memory.execute(plan.sql, dict(plan.parameters))]
    except sqlite3.DatabaseError:
        code = interrupted or "database_unavailable"
        raise QueryError(code, "The bounded query execution could not complete.") from None
    finally:
        memory.set_progress_handler(None, 0)
        memory.close()


def execute_query(payload: dict[str, object] | QueryIntent, target: Path, *, legacy: Path | None = None,
                  source_root: Path | None = None, limits: ExecutionLimits | None = None) -> QueryResult:
    """Fresh proof on every call; fail the whole result, never return partial trust.

    Invalid intent is rejected before file/database access. The trusted application
    supplies paths/policy; neither those nor a prepared SQL plan belong in intent.
    The source transaction remains active through execution and serialization.
    """
    started = time.monotonic()
    intent = parse_intent(payload)
    policy = _policy(limits)
    deadline = started + policy.timeout_ms / 1000
    as_of = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        target = _target(target, legacy)
        with publication.published_read(target, legacy=legacy, source_root=source_root,
                                        limits=policy.read_limits, deadline=deadline) as read:
            catalog = SeriesCatalog(read.catalog_records())
            plan = compile_query(intent, catalog)
            release_ids = read.release_ids(plan.series.series_key)
            if len(release_ids) > policy.max_releases:
                raise QueryError("resource_limit", "Too many publication cohorts contribute to this series.")
            proved_rows: list[dict] = []
            cohort_count = 0
            for release_id in release_ids:
                read.check_budget()
                cohort = read.projected_rows(release_id)
                if not cohort:
                    raise QueryError("publication_unavailable", "A contributing publication cohort is unavailable.")
                cohort_count += len(cohort)
                if cohort_count > policy.max_authorized_rows:
                    raise QueryError("resource_limit", "The verified cohort row budget is exceeded.")
                proved_rows.extend(row for row in cohort if row["series_key"] == plan.series.series_key)
            read.check_budget()
            selected = _execute_memory(plan, proved_rows, policy, read)
            has_more = len(selected) > plan.row_limit
            selected = selected[:plan.row_limit]
            by_id = {row["observation_id"]: row for row in proved_rows}
            rows = tuple(_cited(row, by_id[row["observation_id"]]) for row in selected)
            warnings = []
            if has_more:
                warnings.append("result_truncated")
            periods = Counter(row["period"] for row in proved_rows
                              if intent.period_start <= row["period"] <= intent.period_end)
            if any(count > 1 for count in periods.values()):
                warnings.append("multiple_vintages")
            result = QueryResult(
                status="truncated" if has_more else "ok" if rows else "empty", series=plan.series,
                period_start=intent.period_start, period_end=intent.period_end, rows=rows, row_count=len(rows),
                has_more=has_more, complete=not has_more, warnings=tuple(warnings),
                snapshot_sha256=read.snapshot_sha256, as_of_utc=as_of,
            )
            if len(result.model_dump_json().encode("utf-8")) > policy.max_result_bytes:
                raise QueryError("response_too_large", "The compact UTF-8 result exceeds its byte budget.")
            read.check_budget()
            return result  # Context final checks run before this return can escape.
    except QueryError as exc:
        if exc.code == "ambiguous_series":
            # Catalog candidates have not passed per-cohort proof yet. Even their
            # keys must not become an alternate unproved public catalog.
            raise QueryError("ambiguous_series", "Specify the remaining series dimensions.") from None
        raise
    except publication.PublicationError as exc:
        code = exc.code if isinstance(exc, publication.ReadLimitError) else "publication_unavailable"
        raise QueryError(code, "Fresh protected publication reads are unavailable.") from None
    except (OSError, SQLAlchemyError, sqlite3.DatabaseError):
        raise QueryError("database_unavailable", "The explicit query database could not be read.") from None
    except (ValidationError, KeyError, TypeError, ValueError):
        raise QueryError("publication_unavailable", "The protected result metadata is invalid.") from None


_ERROR_MESSAGES = {
    "invalid_intent": "La requête structurée n’est pas valide.",
    "invalid_catalog": "Le catalogue publié ne peut pas être utilisé.",
    "unknown_series": "Aucune série publiée ne correspond à cette sélection.",
    "ambiguous_series": "Plusieurs séries correspondent : précisez leurs dimensions.",
    "invalid_period": "Les périodes ne correspondent pas au calendrier de cette série.",
    "period_range_too_large": "La plage demandée dépasse 240 périodes.",
    "invalid_execution_limits": "La politique de lecture du serveur n’est pas valide.",
    "publication_unavailable": "La preuve de publication est indisponible ou périmée. Aucun chiffre n’a été retourné.",
    "query_timeout": "La lecture a dépassé sa limite de temps. Réessayez avec une plage plus courte.",
    "resource_limit": "La lecture dépasse les limites autorisées. Réduisez la demande.",
    "database_unavailable": "La base de données ne peut pas être lue pour le moment.",
    "response_too_large": "Le résultat est trop volumineux. Réduisez le nombre de lignes demandé.",
}


def query_response(payload: dict[str, object] | QueryIntent, target: Path, *, legacy: Path | None = None,
                   source_root: Path | None = None, limits: ExecutionLimits | None = None) -> dict:
    """JSON-ready service envelope; no endpoint or broad exception swallowing."""
    try:
        return execute_query(payload, target, legacy=legacy, source_root=source_root,
                             limits=limits).model_dump(mode="json")
    except QueryError as exc:
        code = exc.code if exc.code in _ERROR_MESSAGES else "database_unavailable"
        error = {"code": code, "message": _ERROR_MESSAGES[code]}
        return {"version": 1, "status": "error", "rows": [], "row_count": 0,
                "has_more": False, "complete": False, "error": error}
