# S0.6 Operational visibility

S0.6 makes the dependencies that break user answers visible before users meet
them: a missing or unusable database, a Trust Core schema behind its migrations, a
failed validation run and a failed ingestion job. It adds observation only: no
publication, review, quality-gate or lifecycle code changes, no new dependency, and
no schema change to either database.

## Health endpoints

| Endpoint | Meaning | Status codes |
|---|---|---|
| `GET /health/live` | The process answers HTTP. Checks no dependency, so a database fault never triggers a restart loop. | 200 |
| `GET /health/ready` | This instance can serve users now. | 200 ready, 503 not ready |

Readiness runs read-only checks on every probe:

- `legacy_database` (`SIKA_DB`, the database the MVP routes serve): the file exists,
  opens read-only, has `observations` and `passages` with the served/provenance
  columns, and holds at least one observation. Reasons: `database_missing`,
  `database_unreadable`, `schema_missing`, `schema_incomplete`, `database_empty`.
- `trust_core_schema` (`SIKA_DATABASE_URL`): `skipped`/`not_configured` when unset.
  When set, the database must exist and its `alembic_version` must equal the single
  head shipped in `migrations/`. Reasons: `migration_pending` (with `current` and
  `head`), `unversioned`, `unknown_revision` (database newer than the code),
  `multiple_heads`, `database_missing`, `database_unreadable`, `invalid_url`,
  `unsupported_backend`, `driver_unavailable`, `database_unreachable`.

SQLite files are opened with `mode=ro`; a missing file is reported and never
created. Readiness never migrates or stamps anything. Responses carry check names,
reason codes, revision identifiers and an observation count, never paths, URLs or
credentials. A failing probe also emits a `health.not_ready` error event.
`render.yaml` points Render's health check at `/health/ready`, so a deploy whose
database or schema is broken does not receive traffic.

The legacy validator's 136 known corpus failures do not affect readiness: they are
tracked by the CI baseline (`docs/reports/legacy_validation_baseline.txt`) and the
validation job events below, and are not treated as resolved.

## Structured logs

Every event is one JSON object per line on stderr (`sika.ops` logger), with `ts`
(UTC, milliseconds, `Z`), `level`, `event` and event fields. `SIKA_LOG_LEVEL`
overrides the `INFO` default. Events never include questions, prompts, source text,
exception messages, query strings, credentials or database URLs; free text is
represented only by its length.

| Event | Emitted by | Key fields |
|---|---|---|
| `http.request` | ASGI middleware on every request except successful health probes | `request_id`, `method`, `path`, `status`, `duration_ms`, `outcome` |
| `query.completed` | `/ask`, `/brief`, `/v1/query` | `route`, `mode`, `latency_ms`, `result_count`, `fallback_reason`, `validation_status`, `outcome`, `provider_calls`, `provider_tokens` |
| `job.started` / `job.succeeded` / `job.failed` | `pipeline/extract.py`, `extract_xlsx.py`, `extract_bceao.py`, `validate.py` | `job_id`, `kind`, `status`, `reason`, `duration_ms`, document/observation counts, `error_type` |
| `job.document.processed` | ingestion jobs, once per document | `job_id`, `source_doc`, observation counts |
| `health.not_ready` | `/health/ready` when a check fails | failed checks and reasons |

Request IDs: a well-formed inbound `X-Request-ID` (8–128 of `A-Za-z0-9._-`) is reused
for correlation; anything else is replaced by a UUID. The ID is returned in the
`X-Request-ID` header and shared by the request event, the query record and the
`/v1/query` response body.

Query record vocabulary:

- `mode`: `deterministic`, `llm`, `protected` (`/v1/query`) or `none`.
- `fallback_reason`: `provider_not_configured`, `database_unavailable`,
  `provider_error`, `no_rows` (brief), or `null` when no fallback was used.
- `validation_status` describes the query plan, not the corpus: `not_applicable`
  (server-owned deterministic SQL), `accepted`, `rejected` (non-`SELECT`),
  `execution_error`, `no_sql` (legacy model-proposed SQL), or for `/v1/query`
  `accepted` or the refusal code it returned (`query_disabled`, `unknown_series`...).
- `outcome`: `answered`, `no_data`, `database_unavailable`, `provider_error`,
  `refused`, `unavailable`, `cancelled` or `error`.
- `provider_tokens`: the provider-reported total token count when available, as the
  cost attribution input; no currency cost is computed.

Job failures: an exception or non-zero exit marks the job `failed` with `reason`
`exception`, `exit` or `interrupted` and the exception type only. A validation run
that finds hard failures completes but is reported as `job.failed` with
`reason=hard_failures` and the counts.

## Limits

- Logs are the only sink. There is no metrics store, dashboard, alerting rule,
  retention policy or persistent query/job table yet; the operator's log platform
  must alert on `level=error`, `job.failed` and `health.not_ready`.
- No organization or user identifiers exist yet, so records carry none; tenant usage
  metering remains S2 work.
- Worker health is not checked: there are no background workers yet.
- Trust Core CLIs (`sika.migrate_inflation`, `sika.validate`, `sika.review`,
  `sika.source_review`) are not instrumented; this ticket did not touch publication,
  review or lifecycle code.
- PostgreSQL readiness is implemented through SQLAlchemy/Alembic but only its
  failure paths are tested; no PostgreSQL driver is a declared dependency.
- Readiness checks schema and row presence, not data freshness or correctness.
- `provider_tokens` is reported usage, not a billed amount.
