# S0.4: Publication quality gate

Status: S0.4.1 technical validator implemented. Review/publication enforcement and
source-value certification remain the next bounded increments.

## S0.4.1 behavior

`sika.quality` checks persisted V2 records independently of the importer's PASS and
records a completed `quality_runs` row for each selected release. The command requires
an explicit existing target; it refuses the legacy schema and never creates or
migrates a database. It does not load `.env` or call a provider.

Hard checks cover normalized calendar periods and frequency, finite representable
values, concept/unit metadata, all six dimensions, source methodology/base-year
coherence, missing references, duplicate complete series identities/keys, duplicate
identity-period-release observations, original labels/pages, archived PDF SHA-256,
physical page bounds, passage references and migration accounting. Every canonical
record is examined, including drafts and quarantined canonical states.

For migrated releases, supply the exact legacy snapshot. The checker compares its
read-only SHA-256 with `inflation_migration_runs.legacy_database`, then verifies every
selected observation/passage ID and original JSON. It detects deleted quarantine,
orphan audit, missing/extra links, unexplained merges, changed values/periods/citations
and changed identities. The original mapping rules are replayed from the versioned
registry; the structural and numeric checks are independent of import execution.

Equal figures in different releases remain separate vintages. Labels are not an
identity. An outlier warning uses |z| > 4 within complete series, counting identical
period/value repetitions only once across vintages. It never changes a figure.
Fewer than five canonical rows in a release is a warning; an empty release fails.

Reuse rights other than `allowed`, unverified publication dates and unknown revision
marks are review warnings. Explained legacy quarantines remain visible with counts.
These warnings do not authorize public use. Each report explicitly carries
`publication_authorized: false`; releases/observations keep their lifecycle state and
the validator creates no review decision.

## Evidence and atomicity

One invocation takes a logical snapshot under a SQLite write reservation. The hash
includes canonical catalogs, source/release provenance and states, observations,
passages, legacy links, migration audit, and observed artifact checksums/page counts.
It also includes validator/schema versions and the checked legacy snapshot hash.
Quality-run history, review decisions and execution times are excluded. Rechecking
identical inputs produces identical findings and snapshot hash, while appending new
run IDs and UTC execution times.

Source bytes and the legacy hash are verified again before recording results.
Per-release reports are written in a single SQL transaction: interruption after any
insert leaves no partial batch. Technical hard failures produce completed `failed`
runs. An infrastructure/schema/input error exits separately and never reports PASS.

The selected `--source-root` bounds local file reads; URI paths outside it and remote
storage are rejected. The default is the target's sibling `trust_core_sources/`.
This PDF pilot does not claim Excel/object-storage/PostgreSQL runtime coverage.
Those adapters require their own reviewed extension and integration checks.

## Run locally

```powershell
.\.venv\Scripts\python.exe -m sika.validate --target data/processed/sika_v2.db --legacy data/processed/sika.db --report docs/reports/S0_4_1_inflation_quality.json
.\.venv\Scripts\python.exe -m sika.validate --target data/processed/sika_v2.db --legacy data/processed/sika.db --release-id 3
$env:OPENAI_API_KEY = 'test'
.\.venv\Scripts\python.exe -m pytest -q
```

Exit codes: 0 for completed technical checks without hard failures (warnings allowed),
1 for completed hard failures, 2 for configuration/I/O/schema errors. `--release-id`
can be repeated; omitted, it records all releases. Empty/nonexistent selections fail.
Global catalog or migration-accounting findings affect every selected release.

The JSON export is written after the SQL transaction. If export fails, the quality
history remains recorded and another invocation can produce a fresh report. Do not
overwrite an older quality run to regenerate an export.

## Local corpus result

The [committed quality report](reports/S0_4_1_inflation_quality.json) records the
checks on the S0.3 snapshot: 242 canonical observations across four releases, with
zero technical hard failures. The 76 legacy quarantines remain preserved and reported.
Sixteen warnings cover those quarantines, unknown reuse rights, publication dates,
revision marks, and the two-row BCEAO subset. All canonical lifecycle states remain
`draft`; no approval is asserted.

The full regression suite passes 179 tests, including 42 dedicated quality tests.
These cover malformed values/periods, missing references, duplicate identities,
invalid citations, missing/changed/out-of-archive sources, audit completeness, stable
snapshot replay, CLI exit codes and rollback of interrupted quality-run batches.
No live provider calls are made.

The legacy MVP database retains SHA-256
`47663a2967c4193a272aa24fc053ab8f272c2f4ba555006accb2a86d0f5073ab`.
Its existing 136 legacy-validator hard findings and eight warnings are unchanged;
the old checker is not applied to V2 tables. Source certification remains necessary:
checking byte provenance and migration fidelity cannot establish that every earlier
extraction correctly read its PDF.

## Remaining S0.4 increments

S0.4.2 must protect finalized quality evidence, bind authenticated reviewer decisions
to the checked snapshot, reject stale or failed evidence, enforce reuse/review policy,
and make lifecycle transitions transactional. Direct status assignment must not bypass
the gate. A technical PASS alone must never allow publication.

S0.4.3 must record source-page spot checks, resolve revision marks and source rights
from evidence, triage quarantine individually, and approve only justified series.
The future S0.5 public query service must read only approved/published observations.
S0.4 stays in progress until its full publication definition of done is verified.
