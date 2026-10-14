# S0.5.2 — Protected query execution and cited results

Status: complete within the synthetic-publication-fixture scope. Verification on
2026-10-14: 654 tests pass, including 193 new S0.5.2 tests; one existing dependency
deprecation warning remains. No MVP API/UI route, real database, publication state or schema
is migrated by this brick. No provider call or new dependency is required.

The maintainer approved continuing the synthetic-only sequencing exception while
S0.4.3 remains open for reuse/date/revision evidence and genuine operator review.
[ADR 0005](adr/0005-execute-queries-from-protected-snapshots.md) records the execution
boundary; [S0.5.1](S0_5_QUERY_COMPILER.md) remains the pure intent/compiler contract.

## Service boundary

`sika.query_execution.execute_query(intent, target, ...)` accepts the same strict
version-1 intent, not a `PreparedQuery`, raw SQL, rows, catalog, proof or read session.
It revalidates intent before opening any file/database. Paths and execution policy are
trusted application arguments, never fields in the request. The committed MVP target
and a supplied legacy target are refused. There is no default database or `.env` load.

`query_response()` provides a JSON-ready success or a sanitized French error envelope.
Expected validation, publication, database, timeout and resource refusals return no
rows and no partial numeric result. Programming errors are not broadly swallowed.
Unknown/missing published series are distinct from a valid published series with an
empty requested period range. No draft identity catalog is returned to the caller.

## Coherent publication proof

The `publication.published_read()` context opens one local SQLite `BEGIN IMMEDIATE`
transaction, then enables `query_only`. The ordering matters: acquiring the writer
reservation after enabling query-only is not allowed by SQLite. Reserving the writer
blocks concurrent review/QA/catalog/lifecycle writes, including in WAL mode, while
the request reads. It is locking, not a persistent data write; no write capability is
granted. Cleanup rolls back, removes progress handlers and disposes the connection.

The context bounds source inputs before reflection, JSON decoding and full QA/source
inspection. It reads the catalog and contributing publication cohorts from the same
held snapshot. Catalog identities come only from published-release candidates; this
is not permission to read their numeric observations.

For the resolved series, **every published contributing release is checked**, even
when a particular release has no observation in the requested period range. Each
projection calls the existing `published_rows()` retained-proof checks with a private,
active session. Missing/incomplete publication, changed content/source policy, failed
latest QA, revoked publisher or later review invalidates the whole request. A valid
first release never becomes a partial answer if a second contributor fails.

The service constructs citations and identities only from that checked snapshot.
It does not reread raw observations after proof and does not cache proof between calls.
Before the result can escape the context, source/legacy bytes and the reviewer allowlist
are rechecked. A closed session cannot be reused and cannot authorize another target.
Private session objects are trusted Python implementation details, not SaaS credentials
or serialized access tokens. Filesystem/DB owners remain privileged, as in ADR 0003.

## Ephemeral SQL execution and result contract

The compiler's fixed template runs on a separate in-memory `authorized_observations`
relation populated exclusively with fresh proof-checked rows. Nothing creates a TEMP
table or view in the source database. No caller chooses the SQL/table/order/columns.
Values are stored as text in that relation, validated as `Decimal`, then serialized as
JSON **strings** to avoid a new binary-float conversion. This does not recover precision
already lost by SQLite storage; PostgreSQL runtime precision remains unverified.

A success contains version, status, canonical series metadata, requested period range,
cited rows, actual `row_count`, `has_more`, `complete`, warnings, snapshot hash and UTC
read timestamp. Every row retains observation/series/release identity, period, value,
unit and revision mark. Its citation includes exact document, physical page, locator,
source URL, publisher, attribution, license URL, release key/date, document SHA-256 and
publication-event ID. These are actual retained metadata, not model-generated evidence.

- `ok`: all selected rows within the requested row cap were returned.
- `empty`: the published series exists but has no row in the requested range. No value
  or forecast is invented.
- `truncated`: the internal `limit + 1` sentinel detected more rows. It is removed;
  `has_more` is true, `complete` is false, and `result_truncated` is explicit.
- Multiple vintages remain separate rows, ordered by period/release/observation ID.
  `multiple_vintages` warns when the requested range contains repeated periods.

`complete` means **not row-truncated**, not gap-free historical coverage. This brick
does not invent a latest-vintage policy, aggregate, convert units, make a forecast or
build an unqualified chart. A future chart adapter must preserve/group vintages and
must not present truncated rows as a complete series. Transport/chart integration is
not delivered here.

## Resource policy and limitations

Trusted callers can lower but cannot exceed the default hard ceilings:

| Boundary | Maximum/default |
|---|---|
| Requested rows / periods | 500 rows plus one internal sentinel / 240 inclusive periods |
| Contributing releases / verified cohort rows | 16 / 10,000 |
| Compact UTF-8 success result | 262,144 bytes; minimum configurable cap 1,024 |
| Snapshot + QA/review/publication rows | 50,000 total |
| Snapshot text/JSON total / one cell | 16 MiB / 65,536 bytes |
| Reflected tables | 32 |
| One artifact / summed artifacts / legacy file | 32 MiB / 64 MiB / 64 MiB |
| Overall cooperative deadline / lock wait | 5,000 ms / 250 ms |
| Source SQL / ephemeral execution VM steps | 5,000,000 / 1,000,000 |

SQLite progress callbacks bound SQL work and time; lock waits are bounded. Deadline
checks also occur between source parsing/hash/validation operations and before return.
They are **cooperative**, not a hard kill/OS timeout for an individual PDF parser,
filesystem operation or Python serialization. Artifact size caps are preflight checks,
not sandboxing against privileged concurrent filesystem changes. Source archives must
remain administrator-controlled and immutable in production. A parser worker/hard
timeout and workload benchmarks are still needed before exposing this service remotely.

Source-size preflight includes immutable QA reports, publication evidence and review
history, not just observation tables. A separate read-only legacy reader applies the
same row/total-byte/cell ceilings independently to legacy observations/passages before
materializing them, sharing the SQL/deadline budget. It requires physical tables,
refuses active journals and rechecks the file hash; importer behavior is unchanged.
Limits and forged/copied policy objects are
revalidated. Oversized responses fail completely instead of silently dropping citation
fields. Ambiguity errors do not expose candidate keys: those catalog candidates have
not passed individual publication proof yet. The standalone compiler's internal
deterministic candidate list is not a public discovery endpoint.
HTTP frameworks must preserve this compact encoding or enforce their own byte limit
after reserialization; this module does not enforce HTTP body/request rate limits.

The writer reservation intentionally favors consistency over throughput. No production
latency, concurrency, scalability, tenant isolation, SaaS authentication or PostgreSQL
certification follows from local SQLite fixture tests.

## Verification plan

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_publication_read.py tests/test_query_execution.py -q
cmd /C "set OPENAI_API_KEY=test&& .venv\Scripts\python.exe -m pytest -q"
```

- Unit/contract checks: strict/forged intent and policy, no-I/O invalid requests,
  deterministic ambiguity/errors, output types and decimal serialization.
- Integration checks: genuine synthetic QA/approve/publish flow, cited structured
  golden-question equivalent, bounds/order, multiple vintages, empty range, sentinel
  stripping, response size and immutable source DB hash.
- Adversarial checks: stale/revoked/failed proof, altered catalog/source, incomplete
  cohort and one invalid sibling; final allowlist/artifact checks; transaction/session
  reuse refusal; lock/VM/row/byte/file/table limits and cleanup after failure.
- Regression checks: legacy API/golden questions, schema, QA and publication tests;
  unchanged real DB hashes. Test approvals certify only synthetic temporary fixtures.

Still required before public integration: complete S0.4.3 evidence/review, interpreter
and API golden-question parity, HTTP limits/timeouts, vintage-aware chart/transport
contracts, workload benchmarks and the production database/identity adapter. S0.5 and
the S0/S1 product gates are not declared complete by S0.5.2.
