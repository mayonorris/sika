# S0.5.3 — Natural-language interpretation and fixture API

Status: complete for synthetic publication fixtures only. This does not enable
real Trust Core data, migrate `/ask` or `/brief`, change the UI, or complete S0.5.
[ADR 0006](adr/0006-interpret-fixture-queries-through-isolated-api.md) records the
decision. [S0.5.2](S0_5_2_QUERY_EXECUTION.md) remains the execution/proof contract.

## Supported interpretation

`sika.interpretation.interpret_question()` is a pure, deterministic French/English
interpreter. It consumes the entire question with a closed grammar, returns the
existing strict `QueryIntent`, and never opens a file, queries a catalog, generates
SQL, or calls a provider. Supported examples include:

- `Quelle est l'évolution de l'inflation au Togo depuis 2023 ?`
- `How has inflation in Togo evolved since 2023?`
- `Inflation du Togo en 2026`
- `Show year-on-year inflation in Togo from 2023 to 2026`
- `Variation mensuelle des prix au Togo en juin 2026`
- `IHPC base 2023 au Togo de janvier 2026 à juin 2026`
- `Inflation au Togo de T1 2025 à T2 2026`

The five concepts are CPI, year-on-year inflation, month-on-month inflation,
three-month price change, and twelve-month average inflation. Geography names/codes
cover the eight WAEMU countries and WAEMU itself. Named months, ISO months, full
years, explicit quarters, inclusive ranges and `depuis`/`since` are supported.
Explicit quarters currently support year-on-year inflation only.

The following linguistic assumptions are disclosed as response codes:

| Code | Meaning |
|---|---|
| `headline_all_items` | All items, all sectors, total aggregation, seasonal adjustment not reported |
| `headline_inflation_yoy` | Bare inflation means year-on-year inflation, not monthly or average inflation |
| `year_expanded_to_months` | A year requests January–December monthly rows, not an annual aggregate |
| `since_ends_at_current_month` | Open monthly range ends at the trusted current UTC month |
| `since_ends_at_current_quarter` | Open quarterly range ends at the trusted current UTC quarter |

No source variant or CPI base is guessed from available rows. An explicit base
2014/2023 can appear in a CPI question or the `price_basis` filter. Conflicting
bases fail. The optional `source_variant` filter is an explicit code, not authority
to publish a series. Missing geography/period requests clarification; ambiguous
identities require explicit disambiguation without exposing unproved candidate keys.
There is no latest-vintage selection, aggregation, forecasting, comparison,
multi-country request or general conversational interpretation. Unknown modifiers
are refused rather than dropped (unsupported wording or invalid period tokens).

## HTTP contract

`api.main` registers `POST /v1/query` before the static mount. It **always returns
503 `query_disabled` in the normal application**. Environment variables cannot
enable it. Legacy routes and their existing data boundary remain unchanged.

A separate trusted test application can include
`build_query_router(QueryFixtureSettings(...))`. Configuration requires a fixture
root and target `Path`; optional source/legacy paths, lowerable `ExecutionLimits`
and an injected `date` are trusted Python settings, never request fields. The
default source archive is `target.parent / "trust_core_sources"`. All paths must
resolve inside the fixture root. Known real databases, their hardlink/symlink
aliases and outside paths are refused. Startup validates configuration; each
execution rechecks existence and confinement. Missing targets are not created.
A trusted administrator choosing arbitrary files is not automatically proving
that those files are synthetic. Do not use this helper to activate real data.

Request example (synthetic application only):

```json
{
  "version": 1,
  "question": "Quelle est l'évolution de l'inflation au Togo depuis 2023 ?",
  "limit": 100,
  "language": "auto",
  "filters": {}
}
```

Allowed fields are exactly version, question, limit, language and filters;
filters permit only `source_variant` and `price_basis`. Defaults are version 1,
100 rows, automatic language and empty filters. Types are strict; boolean limits
or versions are not integers. Questions are trimmed, 3–1000 characters, with
control/format characters rejected. Unsupported non-Latin text is not silently
removed. Constructed/copied models and nested filters are revalidated.

Transport checks occur before execution: uncompressed UTF-8 `application/json`,
valid JSON with no duplicate keys/nonfinite numbers, no query-parameter overrides,
at most 8192 actual streamed bytes and two seconds to read the body. Declared
length never bypasses the actual-byte cap. SQL, paths, policy, catalog, rows,
clock, session and proof are not request fields. Disabled requests do not read
their body or open a database.

A successful response preserves the S0.5.2 result fields and adds:

- `language`: French/English response language.
- `interpretation`: interpreter version, normalized typed intent and assumptions.
- `answer`: deterministic first/last returned-row figures with document/page
  citations; gaps, truncation and multiple vintages are explicitly qualified.
- `request_id`: server-generated UUID, also in the response header.

Decimal values remain JSON strings. Exact citations, release/revision identity,
snapshot hash, timestamp, row count, `has_more`, `complete` and warnings are retained.
No chart is emitted: vintage-aware chart/UI migration is a later increment.
`complete` means not row-truncated, not gap-free historical coverage. Results and
errors use compact UTF-8 with `Cache-Control: no-store` and `nosniff`. The entire
encoded success envelope, including interpretation/answer, is capped at the service
policy (default 262144 bytes); oversized replies fail without dropping provenance.

Errors contain version, status `error`, an empty rows list, zero row count,
`has_more=false`, `complete=false`, language, request ID and a safe code/message.
Clarification may include allowlisted field names. No raw exception, question,
local path, reviewer identity or unproved candidate ID is echoed. Refusal logs
contain only request ID and stable code.

| HTTP | Examples |
|---|---|
| 400 / 408 / 413 / 415 | Invalid JSON/length, body timeout, byte limit, media type |
| 422 | Invalid question/period, missing fields, unsupported wording, unknown/ambiguous series |
| 429 | Physical worker occupied; `Retry-After: 1` |
| 503 | Disabled route, unavailable database/catalog/policy or invalid publication proof |
| 504 | Execution wait budget exceeded |
| 500 | Unexpected programming failure, sanitized at the HTTP boundary |

## Publication and resource safety

Every valid request calls `execute_query()` afresh. Catalog and all contributing
cohorts remain in the held publication snapshot; changed sources, QA, approval or
reviewer authorization refuse the entire result. No status-only shortcut, cache,
raw-observation read or unproved discovery endpoint is introduced.

One dedicated thread and a nonqueueing semaphore bound execution per configured
router. The physical worker owns capacity through actual cleanup. HTTP timeout
(six seconds) or cancellation does not free the slot or start replacement work
while source verification continues. Shutdown waits for physical completion.
SQLite/parser deadlines remain cooperative: there is no hard kill for a stuck
filesystem/PDF/Python operation. This local pilot is not a production throughput,
authentication, tenant, distributed-rate-limit or PostgreSQL certification.

## Verification

### Local interactive fixture sandbox

Run from the repository root with the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -m dev.fixture_sandbox --port 8001
```

Open `http://127.0.0.1:8001/`. This is a separate application, not the legacy UI on
port 8000. The page submits questions to the real protected `/v1/query` route and
shows normalized intent, assumptions, cited rows and explicit failure states.
PDF links open only the allowlisted local synthetic sources. No chart is emitted.

Every launch creates a new unique database/archive below the ignored
`data/processed/fixture_sandbox/` directory. The launcher cannot select or reuse an
existing database, enroll a reviewer, read `.env`, import `api.main` or create a
provider client. It binds only loopback, rejects foreign Host/Origin headers and
does not expose its database directory through a static mount. The normal app's
new query route remains disabled. The files are retained locally for inspection;
the next launch builds a fresh environment rather than editing a previous run.

All **511 invented observations** are clearly labeled synthetic: five monthly
concepts for Togo and Benin (January 2023–October 2026), an additional Togo CPI base
2014 series, and five revised Togo year-on-year observations. Geography labels are
used to test interpretation, not to claim actual economic figures. The clock is
frozen at **2026-10-15** for reproducible `since` questions, and there are 11 series.
Other grammar-supported countries need not have fixture data.

Examples to try:

- `Quelle est l'évolution de l'inflation au Togo depuis 2023 ?`
- `How has inflation in Togo evolved since 2023?`
- `Monthly inflation in Benin in June 2026`
- `IHPC base 2023 au Togo de janvier 2026 à juin 2026`
- `IHPC au Togo en 2026` — ambiguous: no base is guessed.
- `Inflation du Togo en 2030` — valid series, empty period range.
- Select a row limit of 2 — explicit truncation, not an apparently complete series.
- `Inflation en 2026` — clarification, since the country is missing.
- `Forecast inflation in Togo for 2030` — unsupported, no provider fallback.

The test process simulates only a clearly synthetic reviewer/allowlist, never an
OS operator enrollment. It adds uniquely named synthetic source profiles without
altering official profiles. Every printed row/value/page/revision is verified
against the committed synthetic PDFs before real QA/approval/publication service
calls; no gate or protected state is patched. The simulated context remains alive
while serving so each request must still pass fresh proof checks. It is restored
on shutdown. None of this is real source certification or reuse permission.

The three tiny PDF fixtures contain only invented data and are committed under
`dev/fixtures/`. The running sandbox needs no new dependency. If fixture values
change, regenerate their sources using `python -m dev.generate_fixture_sources`
with a development runtime containing ReportLab; then recheck all printed rows,
pages and visual layout. Do not change official source profiles to make tests pass.

Manual-environment verification on 2026-10-15: all 37 dedicated sandbox tests pass,
including fresh source/reviewer revocation, physical fixture counts, scoped identity
restoration and read-only source/DB invariance. French and English questions were
also exercised in the browser, and the generated source pages were rendered and
inspected. Both real DB hashes remain unchanged. The complete regression run had
951 passes and one recovery assertion still using an artificial 30 ms test timer.
Restoring the normal budget for that recovery step makes both timeout/cancellation
cases pass on focused rerun; no service deadline or guard was loosened. This records
the full run plus targeted recheck, not a second complete post-correction suite run.

### Automated regression

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_fixture_sandbox.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_interpretation.py tests/test_query_api.py -q
cmd /C "set OPENAI_API_KEY=test&& .venv\Scripts\python.exe -m pytest -q"
```

Tests use temporary synthetic publications through genuine QA/approval/publication
services. They cover FR/EN golden-question-1 equivalents, exact cited decimals,
empty/truncated/vintage states, fresh proof revocation, invalid sibling cohorts,
ambiguity, strict malformed/streamed bodies, body timeout/disconnect, configuration
aliases/forgery, final envelope bytes, sanitized errors and physical slot retention
after timeout/cancellation. Provider calls and real-data approvals are not used.

Validation on 2026-10-15: **915 tests pass**, including 170 interpreter and 91 HTTP
tests added by this brick, in the full provider-free regression run. One existing
Starlette/httpx deprecation warning remains. Both real database SHA-256 hashes are
unchanged; no real row, source policy or approval state was modified. See also
`BACKLOG_SAAS.md`. S0.4.3 evidence/review, complete
golden-question parity, vintage-aware rendering, production identity/worker/runtime
and an explicitly reviewed real-data activation path remain open. S1 is not unlocked.
