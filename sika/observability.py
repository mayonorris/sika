"""Structured operational logs: requests, ingestion/validation jobs and query records.

Every event is one JSON object per line on stderr, with a UTC timestamp and the
request or job identifier. Events never carry questions, prompts, source text,
credentials or database URLs: only identifiers, counts, durations, status codes
and closed-vocabulary reason codes. This is S0.6 operational visibility, not a
metrics backend, persistent audit log or tenant usage meter.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

LOGGER_NAME = "sika.ops"
_REQUEST_ID: ContextVar[str | None] = ContextVar("sika_request_id", default=None)
_INBOUND_ID = re.compile(r"[A-Za-z0-9._-]{8,128}")
_HANDLER_MARK = "_sika_json_handler"
# Query outcomes that mean a dependency or the service failed, not the question.
_FAILED_OUTCOMES = frozenset({"error", "provider_error", "unavailable", "database_unavailable"})


class JsonFormatter(logging.Formatter):
    """Render a record as a single JSON line; extra fields come from ``fields``."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}) or {})
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


class _StderrHandler(logging.StreamHandler):
    """Write to the current sys.stderr, so redirected/captured streams still work."""

    def __init__(self) -> None:
        super().__init__()

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:
        pass


def configure_logging() -> logging.Logger:
    """Attach the JSON handler once. SIKA_LOG_LEVEL overrides the INFO default."""
    logger = logging.getLogger(LOGGER_NAME)
    if not any(getattr(handler, _HANDLER_MARK, False) for handler in logger.handlers):
        handler = _StderrHandler()
        handler.setFormatter(JsonFormatter())
        setattr(handler, _HANDLER_MARK, True)
        logger.addHandler(handler)
    level = os.getenv("SIKA_LOG_LEVEL", "INFO").upper()
    logger.setLevel(level if level in logging.getLevelNamesMapping() else "INFO")
    logger.propagate = False
    return logger


def log_event(event: str, level: int = logging.INFO, **fields: object) -> None:
    """Emit one structured event; the current request ID is added when present."""
    request_id = _REQUEST_ID.get()
    if request_id is not None and "request_id" not in fields:
        fields = {"request_id": request_id, **fields}
    logging.getLogger(LOGGER_NAME).log(level, event, extra={"fields": fields})


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


class RequestLogMiddleware:
    """Pure ASGI middleware: request ID, one ``http.request`` event per HTTP request.

    Only the method, route path (never the query string), status and duration are
    logged. A well-formed inbound X-Request-ID is reused for correlation; any other
    value is replaced. Successful health probes are not logged to avoid noise.
    """

    def __init__(self, app, *, quiet_paths: tuple[str, ...] = ("/health/live", "/health/ready")) -> None:
        self.app = app
        self.quiet_paths = quiet_paths

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inbound = next((value.decode("latin-1") for key, value in scope.get("headers", ())
                        if key == b"x-request-id"), "")
        request_id = inbound if _INBOUND_ID.fullmatch(inbound) else str(uuid4())
        token = _REQUEST_ID.set(request_id)
        started = time.perf_counter()
        status: int | None = None

        async def send_with_id(message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", ()))
                if not any(key.lower() == b"x-request-id" for key, _ in headers):
                    headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        outcome = "completed"
        try:
            await self.app(scope, receive, send_with_id)
        except BaseException as exc:
            outcome = "cancelled" if type(exc).__name__ == "CancelledError" else "error"
            status = status or 500
            raise
        finally:
            path = scope.get("path", "")
            if not (path in self.quiet_paths and status is not None and status < 400):
                level = logging.ERROR if (status or 500) >= 500 else (
                    logging.WARNING if (status or 0) >= 400 else logging.INFO)
                log_event("http.request", level, request_id=request_id,
                          method=scope.get("method"), path=path, status=status,
                          duration_ms=_elapsed_ms(started), outcome=outcome)
            _REQUEST_ID.reset(token)


@dataclass
class JobRun:
    """Mutable state of one ingestion or validation job while it runs."""

    kind: str
    job_id: str = field(default_factory=lambda: str(uuid4()))
    fields: dict[str, object] = field(default_factory=dict)
    failure: str | None = None

    def update(self, **fields: object) -> None:
        self.fields.update(fields)

    def step(self, event: str, **fields: object) -> None:
        log_event(f"job.{event}", job_id=self.job_id, kind=self.kind, **fields)

    def fail(self, reason: str, **fields: object) -> None:
        """Mark a job failed without an exception (e.g. validation found hard failures)."""
        self.failure = reason
        self.fields.update(fields)


@contextmanager
def job_run(kind: str, **fields: object) -> Iterator[JobRun]:
    """Emit job.started, then exactly one of job.succeeded or job.failed.

    Exceptions (including a non-zero ``SystemExit``) mark the job failed with only
    the exception type, never its message, and are re-raised unchanged.
    """
    configure_logging()
    job = JobRun(kind, fields=dict(fields))
    started = time.perf_counter()
    log_event("job.started", job_id=job.job_id, kind=kind, **job.fields)
    try:
        yield job
    except BaseException as exc:
        if isinstance(exc, SystemExit) and exc.code in (None, 0):
            _finish(job, started)
        else:
            job.failure = job.failure or (
                "interrupted" if isinstance(exc, KeyboardInterrupt) else
                "exit" if isinstance(exc, SystemExit) else "exception")
            _finish(job, started, error_type=type(exc).__name__)
        raise
    else:
        _finish(job, started)


def _finish(job: JobRun, started: float, **extra: object) -> None:
    if job.failure is None:
        log_event("job.succeeded", job_id=job.job_id, kind=job.kind, status="succeeded",
                  duration_ms=_elapsed_ms(started), **job.fields, **extra)
    else:
        log_event("job.failed", logging.ERROR, job_id=job.job_id, kind=job.kind,
                  status="failed", reason=job.failure, duration_ms=_elapsed_ms(started),
                  **job.fields, **extra)


@dataclass
class QueryRecord:
    """One ``query.completed`` record per answered question or brief.

    validation_status describes the query plan, not the corpus:
    ``not_applicable`` (server-owned deterministic SQL), ``accepted``/``rejected``/
    ``execution_error``/``no_sql`` (legacy model-proposed SELECT), or the refusal
    code returned by the protected /v1/query path.
    """

    route: str
    mode: str = "deterministic"
    result_count: int = 0
    fallback_reason: str | None = None
    validation_status: str = "not_applicable"
    outcome: str = "answered"
    provider_calls: int = 0
    provider_tokens: int | None = None
    extra: dict[str, object] = field(default_factory=dict)
    _started: float = field(default_factory=time.perf_counter, repr=False)

    def provider_call(self, response: object) -> None:
        """Count a provider call and its reported token usage, when available."""
        self.provider_calls += 1
        total = getattr(getattr(response, "usage", None), "total_tokens", None)
        if isinstance(total, int):
            self.provider_tokens = (self.provider_tokens or 0) + total

    def __enter__(self) -> QueryRecord:
        return self

    def __exit__(self, exc_type, _exc, _tb) -> None:
        if exc_type is not None and self.outcome == "answered":
            self.outcome = "error"
            self.extra.setdefault("error_type", exc_type.__name__)
        self.emit()

    def emit(self) -> None:
        level = (logging.ERROR if self.outcome in _FAILED_OUTCOMES else
                 logging.WARNING if self.outcome == "cancelled" else logging.INFO)
        log_event("query.completed", level, route=self.route, mode=self.mode,
                  latency_ms=_elapsed_ms(self._started), result_count=self.result_count,
                  fallback_reason=self.fallback_reason,
                  validation_status=self.validation_status, outcome=self.outcome,
                  provider_calls=self.provider_calls, provider_tokens=self.provider_tokens,
                  **self.extra)
