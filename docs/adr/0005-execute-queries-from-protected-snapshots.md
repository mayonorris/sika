# ADR 0005: Execute bounded queries from protected publication snapshots

Status: accepted for the isolated S0.5.2 service and synthetic verification only.
Date: 2026-10-13
Decider: project maintainer through the approved S0.5.2 implementation directive.

## Context

[ADR 0004](0004-prepare-bounded-inflation-queries.md) established a non-executing
typed intent, deterministic canonical resolver and fixed parameterized SQL compiler.
Its `authorized_observations` relation is an adapter contract, not a production view
over raw observations. A prepared query confers no publication authority.

The next independently testable brick must supply fresh publication proof, bounded
execution and cited results without changing the legacy API/UI or real databases.
S0.4.3 still lacks applicable reuse terms, publication-date/revision evidence and
genuine authorized cohort review. The maintainer-approved dependency exception now
extends to this local service on synthetic publication fixtures; it does not authorize
serving real draft data or declaring S0.4/S0.5 complete.

Proof is more demanding than checking a lifecycle column. Publication reads verify
retained content, latest QA, authorization and review history, archived source bytes
and, for migrated facts, the exact legacy snapshot. Catalog resolution, release
selection and the rows returned to the caller must belong to the same consistency
boundary. Checking several releases in independent transactions can mix snapshots.
A deferred SQLite read transaction does not prevent a WAL writer from committing a
new review or invalidating proof while the caller constructs its result.

Local artifacts and database content must also be bounded before expensive snapshot
materialization, JSON decoding or PDF parsing. SQL interruption and cooperative
deadlines do not provide a hard operating-system timeout for a source parser.

## Decision

Add `sika/query_execution.py` as an isolated service, backed by a held protected read
context in `sika/publication.py`. Extend the existing SQLite engine with an explicit
writer-reserved transaction mode; retain its deferred default for existing callers.
No schema migration, dependency, provider call, public route or UI change is included.

### One protected consistency boundary

- Open one explicit local SQLite target supplied by trusted application code. Refuse
  the legacy MVP database as the execution target. The intent cannot supply a path,
  prepared plan, catalog, session or execution policy.
- Reserve the SQLite writer with `BEGIN IMMEDIATE` **before** enabling
  `PRAGMA query_only=ON`. Do not acquire any protected write capability and do not
  persist data or evidence. Roll back and close the context after use. The reservation
  prevents concurrent writers, including in WAL mode, from changing the target while
  catalog resolution, proof checking and result construction take place.
- Run resource preflight before taking the full snapshot. Build the catalog and
  contributing release list from that same held snapshot, not a cached catalog or a
  second unprotected query. Catalog membership identifies candidates, not permission.
- Use an internal active `ReadSession`; it is neither serializable nor accepted from
  a request. Its row projection calls the existing
  `published_rows(..., _session=...)` proof path, retaining the same QA, review,
  authorization and source policies rather than implementing a weaker parallel gate.
- Validate **every published contributing cohort for the resolved series**, even when
  its observations fall outside the requested period. If any contributor has missing
  or invalid retained proof, fail the whole query. Never return a partial trustworthy
  subset or silently hide a broken contributor behind period filtering.
- Keep the protected source context active through fixed-SQL execution, exact citation
  binding, serialization and result-size validation. Recheck source artifacts, legacy
  input and reviewer allowlist before the context closes and the result can escape.
  Deactivate the session on exit so it cannot be reused as a stale capability.

The writer reservation protects database consistency, not files or environment
variables. Final source/legacy/allowlist checks therefore remain necessary. A local
file/database owner is still privileged; this is not SaaS authentication or protection
against an administrator who controls the process and artifacts.

### Ephemeral execution and exact citations

Compile the validated intent internally and execute only its fixed template on a
separate in-memory SQLite `authorized_observations` relation. Populate that relation
solely with source-proven rows for the resolved series. Never execute the compiler
against raw persistent observations, and never turn a status predicate into proof.
Set the memory database query-only after populating the ephemeral relation.

Preserve values as text in the ephemeral relation, convert validated returned values
to `Decimal`, and serialize them as JSON strings. Do not introduce a floating-point
conversion, rescale a value or claim that local SQLite numeric storage certifies
arbitrary-precision financial arithmetic.

Bind each returned observation to its exact proved row. Its citation retains document,
physical one-based page and optional precise locator, source URL, publisher,
attribution and license URL, release key/date, content SHA-256 and publication event
ID. Metadata is not inferred from a filename, a neighboring observation or model text.
Malformed numeric or citation metadata fails the result rather than dropping the row.

Keep deterministic ordering and all publication vintages. Do not select a latest
vintage, deduplicate by confidence, aggregate, forecast or introduce a chart payload.
Report `multiple_vintages` when multiple selected-period observations exist across
vintages, so a future consumer cannot treat them as one unqualified time series.

Remove the compiler's `limit + 1` sentinel and expose `has_more`, `complete` and a
truncated status/warning. `complete` means **not row-truncated**, not gap-free coverage
of the requested calendar or complete history of an official series. An empty period
returns an empty result, never a forecast or an invented zero.

### Bounded work and graceful refusal

Use server-owned, revalidated limits which a trusted caller may lower but never raise
above hard ceilings. Keep compiler bounds of 240 periods and 500 returned rows.
Bound contributing releases, authorized cohort rows, serialized UTF-8 response bytes
and SQL work independently of the requested period. Preflight caps cover table count,
snapshot rows/bytes, individual text/JSON cells, per-artifact and total artifact bytes,
and legacy database size before full materialization or source parsing. The isolated
legacy read also requires physical tables, applies row/cell/total-byte ceilings before
materialization and shares the SQL/deadline budget. It preserves checkpointed read-only
snapshot semantics without changing the importer or administrative publication paths.

Bound lock acquisition with SQLite busy timeout and SQL execution with progress
handlers plus monotonic deadlines. Check the shared deadline between cohort, artifact,
projection and serialization operations. An individual PDF parser or filesystem call
is not forcibly interrupted: these are cooperative checks, not a hard parser/OS wall
clock guarantee. Do not advertise a production latency or throughput guarantee.

`execute_query()` accepts only intent plus trusted service configuration.
`query_response()` provides a JSON-ready envelope with sanitized French errors for
invalid/ambiguous intent, unavailable proof/database, resource limits, timeout and
oversized results. Expected failures return no numeric rows or unproved ambiguous
candidate keys. The compiler's internal ambiguity list is not public catalog proof.
Do not echo submitted
SQL, untrusted input values, paths, source text or underlying exception messages.
This service is not an HTTP endpoint; transport and tenant authorization remain later
integration responsibilities.

## Options considered

| Option | Benefit | Limitation |
|---|---|---|
| Query raw observations using a trusted status filter | Minimal code and database work | Published status alone does not establish fresh QA, source, review or authorization proof; violates the publication boundary |
| Repeated independent `published_rows()` calls per release | Reuses the existing gate with a small adapter | Catalog and cohorts can belong to different snapshots; a WAL writer can invalidate earlier checks before the result is returned |
| Held protected snapshot plus ephemeral authorized relation | Reuses the exact gate and keeps catalog, cohorts and result construction inside one boundary | Requires preflight and final checks; writer reservation can reduce availability and does not impose a hard parser timeout |

## Trade-off analysis

Choose the third option. The ephemeral relation preserves the compiler's narrow,
fixed-SQL interface and prevents accidental reads of unproved persistent rows. The
held transaction avoids cross-cohort mixed snapshots and blocks target writers during
the entire read. Separate source/legacy/authorization rechecks address state not
protected by a SQLite transaction.

This intentionally favors coherent, default-deny results over maximizing concurrent
write throughput. `BEGIN IMMEDIATE` can contend with validation/publication work and
other protected readers; even a query which returns no rows may inspect and validate
more data than its period range suggests. Independent resource caps refuse expensive
input rather than implying that `LIMIT` alone bounds verification work.

Do not generalize this local SQLite policy to PostgreSQL. Production requires a tested
service-role/authorization adapter, a defined consistency policy, immutable archived
artifacts and measurements under realistic concurrency. Hard parser timeouts require
an isolated worker/process with its own resource limits, not merely another deadline
check in this synchronous function.

## Consequences

- Compiled plans, stored catalog membership and old proof cannot authorize execution.
  Broken contributing evidence yields no partial result, including for cohorts outside
  the requested period.
- Exact provenance travels with every numeric row; vintages, truncation and empty
  states remain explicit for later API, export and visualization consumers.
- Source reads do not append QA/review/publication events or mutate lifecycle states.
  Synthetic publication fixtures exercise the genuine gate without certifying rights
  or publishing any real observation.
- SQLite lock contention and cooperative source-parser timeouts are explicit local
  limitations, not deployment readiness. An immutable source archive and isolated
  parser worker remain production follow-up work.
- API/NLP integration, tenant identity/authorization, PostgreSQL runtime support,
  pagination, chart/vintage policy and operational telemetry remain undelivered.
  S0.5 completion and S1 entry still depend on the existing source/publication gates.

## Action items and verification

1. Implement the held protected context, bounded preflight, active-session projection,
   fixed ephemeral execution and cited response envelope without changing either real
   database, schema or the working MVP.
2. Verify synthetic published/empty/truncated results, exact Decimal serialization,
   physical-page citations, deterministic ordering and retained publication vintages.
3. Exercise fresh QA/source/review/authorization invalidation, a broken contributor
   outside the requested period, cross-target/inactive session misuse and concurrent
   writers, including WAL mode. Verify final checks run before a result escapes.
4. Test malformed intent, injection/forged policy attempts, lowerable hard limits,
   oversized rows/text/JSON/artifacts, lock contention, SQL interruption and response
   byte limits. Verify every expected failure contains no numeric rows or private input.
5. Run the existing regression suite without live provider calls and recheck both real
   database hashes. Record measured validation results in the active backlog; this ADR
   is the design decision, not a test PASS or corpus certification report.
6. Before production integration, benchmark verification and lock availability; select
   an immutable artifact strategy and isolated parser worker for hard timeouts; test
   the PostgreSQL service-role/consistency adapter and real authenticated authorization.
7. Resolve S0.4.3 evidence and genuine approval before connecting real inflation data
   to public endpoints. Integrate intent parsing and demonstrate golden-question parity
   in a later brick before retiring the legacy path or marking S0.5 complete.
