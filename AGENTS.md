# AGENTS.md — Instructions for AI coding agents (Codex)

Read this file before any work session. It defines the project context, conventions, and boundaries.

## What this project is

Sika makes official West African economic statistics (INSEED Togo, BCEAO, WAEMU) queryable in natural language. Pipeline: official publications -> structured observations with full provenance -> FastAPI backend -> research workspace with cited answers, charts, briefs, exports, and monitoring. The product started as an OpenAI Build Week 2026 prototype and is now moving toward a multi-tenant SaaS platform. Solo builder: Mayo Kadanga, Economic Statistician Engineer (ISE), Lomé.

Read next: `docs/ARCHITECTURE_SAAS.md` (target architecture and migration rules), `docs/BACKLOG_SAAS.md` (current tickets), `docs/DATA_SPEC_V2.md` (Trust Core schema), `docs/DATA_SPEC.md` (legacy data rules), and `docs/PRD.md` (historical MVP product brief).

Source-of-truth order when documents disagree:

1. This file for working rules and safety boundaries.
2. `docs/ARCHITECTURE_SAAS.md` for the accepted target and migration boundaries.
3. `docs/BACKLOG_SAAS.md` for the active ticket and its definition of done.
4. `docs/DATA_SPEC_V2.md` for the canonical schema; `docs/DATA_SPEC.md` remains the
   contract for legacy extraction and endpoints until their migration is complete.
5. `docs/PRD.md` and `docs/BACKLOG.md` as historical MVP context and regression
   requirements.

## Repo map

```
api/main.py                 FastAPI routes, deterministic fallback, and legacy LLM router
app/index.html              Current single-file French-first UI with Plotly
pipeline/extract.py         PDF -> observations + passages (LLM-assisted)
pipeline/extract_xlsx.py    Deterministic Excel ingestion
pipeline/extract_bceao.py   Deterministic BCEAO bulletin ingestion
pipeline/validate.py        DATA_SPEC validation report and hard-failure exit code
pipeline/quarantine.py      Demotes rows that must not be served
pipeline/spot_check.py      Manual provenance sampling helper
sika/database.py           Trust Core engine with SQLite foreign keys/transactional DDL
sika/inflation_mapping.py  Reviewed, checksum-bound inflation identity rules
sika/migrate_inflation.py  Transactional legacy snapshot import and reconciliation
migrations/                Alembic schema versions, independent of the MVP database
alembic.ini                Explicit-target migration configuration (no default DB)
data/raw/                   Local official source files; intentionally not committed
data/fixtures/              Synthetic development fixtures
data/processed/sika.db      Seeded legacy SQLite database; committed for the demo
docs/ARCHITECTURE_SAAS.md   Accepted SaaS target and migration sequence
docs/BACKLOG_SAAS.md        Active post-hackathon implementation backlog
docs/DATA_SPEC_V2.md        Versioned canonical schema and migration commands
docs/S0_3_INFLATION_MIGRATION.md  Inflation migration results, limits, and replay guide
docs/adr/                  Accepted architecture decisions
docs/BACKLOG.md             Historical golden questions and MVP backlog
tests/                      Extraction, validation, API, data, and UI smoke tests
```

## Commands

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn api.main:app --reload
cmd /C "set OPENAI_API_KEY=test&& .venv\Scripts\python.exe -m pytest -q"
.\.venv\Scripts\python.exe pipeline\validate.py
.\.venv\Scripts\python.exe pipeline\spot_check.py

# Trust Core only: explicit separate database; does not load .env or use SIKA_DB.
$env:SIKA_DATABASE_URL = 'sqlite:///data/processed/sika_v2.db'
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m pytest tests/test_schema_migrations.py -q

# S0.3: first import requires an empty, versioned target. An exact replay is safe.
.\.venv\Scripts\python.exe -m sika.migrate_inflation --target data/processed/sika_v2.db --report docs/reports/S0_3_inflation_reconciliation.json
.\.venv\Scripts\python.exe -m pytest tests/test_migrate_inflation.py -q

# Optional source ingestion. PDF extraction requires a configured provider key;
# deterministic Excel and BCEAO paths do not use the LLM for numeric values.
.\.venv\Scripts\python.exe pipeline\extract.py data\raw\one.pdf
.\.venv\Scripts\python.exe pipeline\extract_xlsx.py
.\.venv\Scripts\python.exe pipeline\extract_bceao.py
```

The application runs without an API key through its deterministic fallback. Routine
tests must not call a live model provider. The extraction-module tests currently set a
placeholder `OPENAI_API_KEY=test` only so the SDK can be instantiated; this is not a
credential and no request may leave the test process. Use a real `OPENAI_API_KEY`,
`OPENAI_BASE_URL`, and `OPENAI_MODEL` only for an explicitly requested integration
or extraction test.

## Hard rules

1. **Never invent a number.** Every figure shown to a user must exist in the `observations` table and carry `source_doc` + `source_page`. If data is missing, the answer says so plainly. This is the product's core promise; breaking it disqualifies the work.
2. **Extraction is not publication.** New rows enter staging or draft state in the target
   architecture. Only rows that pass hard validation and the required review may reach
   public answers, charts, exports, alerts, or briefs.
3. **Never commit secrets.** `.env` stays local. If you touch config, update
   `.env.example` with placeholder values only. Do not log keys, full prompts, private
   source text, or personal/payment data.
4. **No model-authored SQL in the target architecture.** Until the safe query compiler
   replaces the legacy router, accept only bounded `SELECT` statements, validate before
   execution, and never interpolate user text. New query paths must use typed intent,
   allowlisted fields, parameterized server-owned SQL, and explicit result limits.
5. **Evolve through explicit architecture decisions.** FastAPI remains the application
   core. SQLite and the single-file UI are legacy MVP components to migrate
   incrementally. PostgreSQL, authentication, background workers, object storage, and a
   modular frontend are allowed only through an active SaaS ticket and an accepted ADR
   when the choice has lasting consequences. Do not introduce microservices, Kubernetes,
   or a frontend rewrite opportunistically.
6. **Tenant data is default-deny.** Every new user-owned resource must belong to an
   organization. Enforce authorization in service/database access, not only by hiding UI.
   Tests must prove that one organization cannot read or mutate another's resources.
7. **UI language is French first.** English is accepted in questions and answered in
   kind. Code, comments, schema names, ADRs, and commits are English.
8. **Small commits, imperative messages** (`Add period normalization for quarterly
   labels`). Commit after each independently working brick and do not mix tickets.
9. **Preserve the working MVP while migrating.** Use compatibility layers, reversible
   migrations, and reconciliation reports. Do not combine a platform migration with
   unrelated UI or extraction refactors.

## Code conventions

- Python: type hints on public functions, docstrings where intent is not obvious,
  f-strings, and `pathlib` over `os.path`. Keep route handlers thin and put reusable
  business rules in modules or services.
- Database: use foreign keys, explicit transactions, parameterized queries, and indexes
  justified by query paths. Version every target-schema change with forward and rollback
  behavior; production startup must not create or mutate schema ad hoc.
  Use the engine in `sika/database.py` for Trust Core SQLite connections. Never migrate
  or stamp the committed MVP database. S0.2 is a parallel schema, not a publication gate;
  S0.4 must enforce that gate before public reads. PostgreSQL SQL compilation is tested,
  but a live PostgreSQL migration/rollback is required before deployment on that engine.
  S0.3 imports into a separate empty SQLite target and preserves all unresolved rows
  in migration audit tables. Do not update a source checksum or mapping rule merely
  to make a row pass. Review the original publication, version the mapping, and use
  a new target for changed snapshots. Keep the archived source bytes with the database.
  Reconciliation PASS proves preservation, not publication approval or full source
  certification. The legacy validator still reports pre-existing corpus failures;
  do not silently alter the MVP database or treat those failures as resolved by S0.3.
- Errors: user-facing endpoints must not 500 on invalid input or expected dependency
  failure. Return a structured message the UI can display. Preserve dignified states for
  empty data, API down, timeout, quota, and unsupported questions.
- LLM calls: request structured JSON when the provider supports it, but preserve the
  compatibility fallback for providers that reject `response_format`. Parse
  defensively, retry only bounded transient errors, and fall back deterministically.
- Time and audit: store timestamps in UTC and include request/job identifiers in
  structured logs. Never use extraction confidence as statistical uncertainty.
- Frontend: keep `app/index.html` working until a dedicated frontend migration ticket
  and ADR are approved. Do not start a build step incidentally.

## Working sequence

1. Read this file and the active ticket in `docs/BACKLOG_SAAS.md`.
2. Inspect `git status` before editing and preserve unrelated user changes.
3. State the brick's compatibility boundary and acceptance checks.
4. Implement the smallest end-to-end increment that can be independently verified.
5. Run tests proportional to risk plus any ticket-specific data reconciliation.
6. Update the active ticket status only after its definition of done passes.
7. Commit with an imperative message and push the completed brick.

## Definition of done (any ticket)

- Runs locally via the commands above with no traceback.
- Golden questions in `docs/BACKLOG.md` (historical MVP regression suite) still pass.
- Citations render for every numeric claim.
- No live provider call is required for the default test suite.
- DATA_SPEC validation passes for any corpus or domain touched by the ticket.
- Data migrations are reversible and preserve provenance.
- New tenant-facing behavior enforces organization boundaries and records usage where applicable.
- Documentation and the active backlog describe the behavior actually shipped.
- Committed and pushed.

## Testing quick check

```powershell
cmd /C "set OPENAI_API_KEY=test&& .venv\Scripts\python.exe -m pytest -q"
.\.venv\Scripts\python.exe pipeline\validate.py
.\.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/processed/sika.db'); print(c.execute('SELECT COUNT(*), COUNT(DISTINCT source_doc) FROM observations').fetchone())"
```

For API checks, start Uvicorn and exercise `/indicators`, `/sources`, one relevant
golden question through `/ask`, and `/brief` when the ticket touches synthesis.
Schema, authorization, job, and migration tickets also require their dedicated tests;
the quick check is not sufficient.
