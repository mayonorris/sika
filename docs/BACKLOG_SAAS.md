# Sika SaaS backlog

This is the active post-hackathon backlog. docs/BACKLOG.md remains the historical MVP
and golden-question regression record. Work top to bottom inside an epic unless a
documented dependency changes the order. Every ticket ends with a small working commit.

## S0 — Trust Core

### S0.1 Establish the SaaS architecture contract

Status: complete

- Define the modular-monolith target and migration boundaries.
- Replace obsolete hackathon constraints in AGENTS.md without weakening trust rules.
- Record the target data lifecycle, query safety boundary, and explicit deferrals.
- Define S0 exit criteria and the ordered implementation backlog.

DoD: AGENTS.md, docs/ARCHITECTURE_SAAS.md, and this backlog agree; the historical
MVP docs point readers to the active roadmap; no runtime behavior changes.

### S0.2 Specify the canonical statistical schema

Status: complete

- Write the first schema migration and rollback.
- Add concepts, dimensions, series, sources, releases, observations, quality runs, and
  review decisions.
- Add indexes and database-enforced uniqueness for complete series identities.
- Preserve legacy identifiers and provenance during migration.

DoD: a temporary database can migrate up and down; schema tests prove uniqueness,
foreign keys, and immutable release provenance.

Delivered: Alembic revision `0001_trust_core`, the isolated Trust Core engine,
`docs/DATA_SPEC_V2.md`, and ADR 0001. Legacy row mapping and provenance storage are
ready for S0.3; no real corpus was migrated in S0.2.

Validation: 108 tests pass, including 51 dedicated schema tests covering populated
upgrade/downgrade/upgrade, identity constraints, foreign keys, immutable release
provenance, append-only reviews, SQLite REPLACE guards, and legacy field round-trips.
PostgreSQL upgrade/downgrade SQL generation is checked; a live PostgreSQL migration
test remains required before deployment on that engine.

### S0.3 Migrate the inflation domain

Status: complete

- Map IHPC/inflation rows to canonical concepts, dimensions, series, and releases.
- Preserve source document and page for every row.
- Produce a reconciliation report between legacy and migrated values.
- Quarantine ambiguous category or aggregate mappings.

DoD: all served inflation values reconcile exactly; no unexplained row is silently
dropped or merged.

Delivered: revision `0002_inflation_audit`, checksum-bound deterministic mappings,
transactional snapshot importer, exhaustive row/passage audit, and
[reconciliation evidence](S0_3_INFLATION_MIGRATION.md). The real legacy snapshot has
333 inflation-family rows: 242 canonical observations across 149 series, 15 explained
same-value duplicates, and 76 explicitly quarantined originals. All 55 passages are
preserved. No legacy row is lost, no value is changed and no observation is published.

Validation: 137 tests pass without live provider calls. Populated downgrade/rebuild,
interruption/retry, different publication vintages, corrupt provenance and source
checksums are covered. Replaying the real import produces identical report bytes;
the legacy DB hash is unchanged. Canonical migration checks have zero hard failures
and one small-source warning. The old validator still reports its pre-existing
136 hard findings and 8 warnings on the unchanged full legacy corpus; this baseline
is documented, not silently repaired. Reconciliation is not source certification or
publication approval: S0.4 must resolve review, licensing and revision-state decisions.

### S0.4 Enforce the publication quality gate

Status: in progress (S0.4.1/S0.4.2 complete; S0.4.3 source concordance complete,
publication certification blocked on external evidence and operator review)

- Separate staging observations from published observations.
- Run hard validation after every ingestion job.
- Block publication on invalid period, unit, series identity, missing provenance, or
  unresolved duplicate.
- Store quality runs and reviewer decisions.

DoD: invalid fixtures cannot become public; the migrated inflation corpus has zero hard
failures and an auditable approval.

Delivered S0.4.1: an explicit-target V2 validator records atomic per-release
`quality_runs`, binds findings to the checked snapshot hash, verifies archived PDFs
and physical pages, and reconciles every migrated original against the exact read-only
legacy snapshot. See [quality-gate evidence](S0_4_QUALITY_GATE.md). It neither changes
lifecycle states nor creates reviewer approvals.

Validation: 179 tests pass without live provider calls, including 42 quality checks
covering corrupt periods/values/identities/citations, source changes, missing migration
audit, exact replay, and transaction rollback. The real 242-observation V2 corpus has
zero technical hard failures and 16 explicit review warnings; all observations remain
draft and the legacy DB hash is unchanged.

Delivered S0.4.2: revision `0003_publication_gate`, immutable finalized QA and
append-only publication proof, default-deny local OS-operator authorization,
snapshot-bound approval/publication, atomic cohort transitions and protected
published-only reads. SQL cannot manufacture authoritative evidence, assign
protected states, conceal later reviews through retrograde IDs or replace protected
observations/passages through unique-key collisions. See
[ADR 0003](adr/0003-bind-publication-to-reviewed-snapshots.md).

Validation: 256 tests pass without live provider calls, including 70 publication and
adversarial checks, five administrative CLI tests and populated migration/rollback
coverage. The real separate V2 database was backed up, upgraded and revalidated with
zero hard failures and 16 warnings; [current evidence](reports/S0_4_2_inflation_quality.json)
retains all 242 observations as draft, with no approval or publication event. All old
QA records and original data are preserved, exact S0.3 replay still matches, and the
MVP hash is unchanged. This local SQLite gate is not SaaS authentication or certified
PostgreSQL production support.

Delivered S0.4.3 technical review: a read-only source review packet bound to the same
full QA snapshot, complete canonical/legacy/quarantine ID provenance, 45 independent
checksum-bound anchors and a deterministic IHPC table parser (944 source cells).
All 242 canonical observations match their exact source scope/value. The 76 original
quarantines have an individual documentary triage recommendation. See
[source-certification evidence and limits](S0_4_3_SOURCE_CERTIFICATION.md) and
[quarantine registry](S0_4_3_QUARANTINE_REVIEW.md). No data, QA history, source rights,
revision marks, release dates or lifecycle states are altered by this tool.
Full row packets stay local and ignored; only a redacted ID/count/hash summary is
committed to the public repository. No draft V2 dataset is published by this brick.

Remaining S0.4.3: establish applicable reuse terms and date/revision evidence, and
obtain genuine authorized cohort review. Full numeric concordance and individual
quarantine triage are finished. Publisher clarification drafts are ready but unsent.
Public sources are not assumed commercially licensed or prohibited. All 242 rows
remain draft; S0.4 is not complete until its full publication DoD passes.

Validation of the S0.4.3 implementation: 351 tests pass without live provider calls,
including 68 source-review and 27 table-parser tests; the complete real cohort has
zero source discrepancies and an unchanged QA snapshot. Both DB file hashes and
all existing QA/state counts remain unchanged.

### S0.5 Add the safe inflation query compiler

Status: in progress (S0.5.1–S0.5.3 complete on fixtures; real enablement gated)

- Define a typed query-intent contract.
- Resolve aliases to canonical series identifiers.
- Compile parameterized, bounded SQL owned by the server.
- Return citations from observation provenance.
- Keep the current fallback for unsupported domains while migration continues.

DoD: inflation golden questions pass without executing model-authored SQL; malicious or
oversized intents are rejected predictably.

S0.5.1 is a maintainer-approved dependency exception: prepare the typed contract,
canonical resolver and bounded fixed SQL on synthetic fixtures while S0.4.3 remains
open for rights/date/revision evidence and genuine operator approval. No real V2 rows
become public and the legacy API/UI/databases remain unchanged. See
[the contract and remaining execution boundary](S0_5_QUERY_COMPILER.md) and
[ADR 0004](adr/0004-prepare-bounded-inflation-queries.md).

Delivered S0.5.1: strict versioned intent, bounded reviewed-shape catalog, deterministic
complete-identity/alias resolution, fixed parameterized SQL and immutable non-executing
plans. Canonical codes/names cannot be shadowed by another series' aliases. Periods
are bounded to 240 inclusive points; row limits to 500 plus one internal sentinel.
All vintages and citation fields are preserved. No API/UI/schema or provider change.

Validation: 461 tests pass without live provider calls, including 110 dedicated query
tests on synthetic metadata/in-memory rows. Strict types, forged instances, ambiguity,
identity collisions, injection, calendars, boundaries, provenance and sentinel behavior
are covered. Both real database hashes are unchanged; no publication state is altered.
One pre-existing Starlette/httpx deprecation warning remains.

Delivered S0.5.2 implementation: a local proof-bound execution/result service on
synthetic publication fixtures. One writer-reserved, query-only SQLite context holds
catalog, retained publication proof and citations through isolated in-memory SQL and
result construction. All contributing published cohorts are checked, including those
outside the requested period; any invalid contributor refuses the entire result.
Target and read-only legacy snapshot work is bounded before materialization. Values
remain decimal strings on the wire, vintages/citations are preserved, the limit+1
sentinel is removed and truncation is explicit. Expected refusals have sanitized
French envelopes without rows or unproved ambiguity candidates. No real DB, API/UI,
schema, source policy, approval or publication state is changed. See
[execution contract and limits](S0_5_2_QUERY_EXECUTION.md) and
[ADR 0005](adr/0005-execute-queries-from-protected-snapshots.md).

Validation (2026-10-14): 654 tests pass without live provider calls, including 112
protected-read tests and 81 execution/result tests added by S0.5.2. Genuine synthetic
QA/review/publication flows verify cited decimal results, empty/truncated states,
vintage ordering, stale/revoked/failed/partial proof, final-check races, malicious
intent, lowerable resource limits, lock/SQL deadlines and cleanup. Both real DB hashes
are unchanged and the diff checks pass. One pre-existing Starlette/httpx deprecation
warning remains. No real observation is approved or published by these tests.

Delivered S0.5.3: deterministic full-match French/English interpretation for the five
inflation concepts and nine supported geographies; strict question/filter contracts,
explicit headline/calendar assumptions and no inferred source variant or CPI base.
The separate POST /v1/query router delegates every accepted request to fresh protected
execution. It preserves exact cited decimals, vintages, empty/truncated results and
proof refusals. Streamed request bytes, body time, final encoded response bytes and
actual concurrent execution are bounded. A timed-out/cancelled HTTP task does not
release capacity while its physical worker still verifies source evidence.

The normal application registers the route disabled; only trusted isolated fixture
applications can inject confined configuration. No environment-only activation,
provider call, new dependency, real database/schema/publication change, legacy route
migration or UI change is included. See [the interpretation/API contract](S0_5_3_NATURAL_LANGUAGE_API.md)
and [ADR 0006](adr/0006-interpret-fixture-queries-through-isolated-api.md).

Validation (2026-10-15): 915 tests pass without live provider calls, including 170 new
interpreter tests and 91 new HTTP tests. Genuine temporary QA/approve/publish fixtures
cover FR/EN golden-question-1 equivalents, ambiguity, cited values, revision vintages,
empty/truncated ranges, fresh proof revocation, invalid sibling cohorts, strict body
handling, alias/configuration refusal, final response limits, sanitized errors and
timeout/cancellation slot retention. Both real DB SHA-256 hashes are unchanged.
One existing Starlette/httpx deprecation warning remains; no real row is approved.

Delivered manual-test environment: `python -m dev.fixture_sandbox --port 8001`
creates a fresh, ignored synthetic database and serves a dedicated local page,
without importing/enabling the MVP app. It contains 511 invented observations and
11 series, a frozen 2026-10-15 clock, explicit FR/EN examples, ambiguity/truncation
and empty/refusal states, separate revision vintages and local synthetic PDF links.
All printed fixture values/pages are checked before unchanged QA/approve/publish
services run. Only the dedicated fixture process simulates a synthetic reviewer;
no real operator is enrolled or real source policy/evidence modified. Loopback
Host/Origin checks and hashed allowlisted assets prevent directory/data exposure.
The PDF assets are byte-preserved through Git binary attributes. Source PDFs and
the bilingual UI were inspected visually; normal real-data routes remain unchanged.

Manual-sandbox verification (2026-10-15): all 37 new sandbox tests pass. The complete
run recorded 951 passes and exposed one existing test's artificial 30 ms timeout
being reused for its recovery request. Only that test was corrected to restore the
normal wait budget after the intentional timeout; both cancellation/timeout cases
then pass on focused rerun. No production deadline or publication guard was changed.
Both real DB hashes and all three committed synthetic PDF byte hashes are unchanged.

Remaining S0.5: complete end-to-end golden-question/domain parity, vintage-aware
rendering and a reviewed real-data activation/migration path. Local SQLite source-parser
deadlines are cooperative, not hard worker isolation or production load certification.
Real public answers remain gated on S0.4.3 evidence and genuine operator review;
S0.5 as a whole and the S0/S1 product gates are not complete.

### S0.6 Add operational visibility

Status: queued

- Add liveness and readiness endpoints.
- Add structured request and ingestion-job logs.
- Record query latency, result count, fallback reason, validation status, and cost where
  applicable.
- Add a CI workflow for tests, schema migration checks, and corpus validation.

DoD: a failed database dependency, migration, validation run, or ingestion job is visible
before it affects users.

## S1 — Inflation Watch

Queued after S0 exit criteria are met:

1. Series catalog and trustworthy series pages.
2. Authentication, organizations, and memberships.
3. Persistent workspaces and saved series/charts.
4. CSV export with complete provenance metadata.
5. Release watchlists and email alerts.

## S2 — Paid pilot

Queued after at least three design partners use S1:

1. Scheduled briefs and team sharing.
2. Usage metering, quotas, plans, and entitlements.
3. Manual invoice tracking behind a provider-neutral billing interface.
4. Support/admin tools, audit logs, and initial service objectives.

## S3 — Regional scale

Queued after paid-pilot retention and data freshness are demonstrated:

1. Automated source discovery and ingestion scheduling.
2. Public API keys, webhooks, and bulk delivery.
3. Country expansion driven by contracted series demand.
4. SSO, private sources, connectors, and contractual SLAs.

## Product gates

- Do not start S1 until migrated inflation data is published through the quality gate.
- Do not automate billing until at least one pilot agrees to pay.
- Do not promise country coverage without a measured freshness SLA for its sources.
- Do not add forecasting while Sika's promise remains official observed statistics.
