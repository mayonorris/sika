# S0.4: Publication quality gate

Status: S0.4.1 and S0.4.2 complete for the local SQLite pilot. Source-value
certification remains S0.4.3; the complete S0.4 publication DoD is still pending.

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
.\.venv\Scripts\python.exe -m sika.validate --target data/processed/sika_v2.db --legacy data/processed/sika.db --report docs/reports/S0_4_2_inflation_quality.json
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

At the S0.4.1 delivery, the regression suite passed 179 tests, including 42 dedicated quality tests.
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

## S0.4 review and publication

### S0.4.2: Local operator review and publication

See [ADR 0003](adr/0003-bind-publication-to-reviewed-snapshots.md). Migration
`0003_publication_gate` retains old reports and creates append-only publication events.
Old S0.4.1 reports cannot authorize new publication: run the current validator again.
Final quality records cannot be edited, deleted or replaced; authoritative completion
requires the validation service. Protected states cannot be inserted or assigned by
ordinary SQL. Reviewed/published facts and their passage cohort are frozen.
INSERT and UPDATE collision checks cover all protected unique keys, including legacy
passage IDs, even with recursive triggers disabled. Review IDs must increase strictly;
a caller cannot conceal a later veto by supplying a lower or negative identifier.

`sika.publication.approve()` requires the latest current-version PASS on the exact
global snapshot and an allowlisted OS principal. The release needs a verified date,
known revision marks, reuse status `allowed`, a licence evidence URL and attribution.
The operator supplies source-value/revision/rights reasons, exact IDs for every verified
observation, and a reason for every current warning. No warning acknowledgement waives
the date, revision or rights policy. Entire cohorts transition together.

`publish()` consumes the returned **review decision ID**, not the event ID. It verifies
the approved release-local content hash and fresh hard checks, and requires the same
latest quality run. New QA, later review, changed facts or changed source bytes require
a new approval. Before/after global hashes audit each transition; the local content
hash excludes lifecycle state only, so an unrelated draft vintage does not destroy an
unchanged release's approval. All state/event/decision writes share one transaction.

`published_rows()` is the future query service boundary, not a replacement MVP endpoint.
It returns only a fully published cohort with intact proof, fresh validation, a valid
latest QA, an authorized publisher and no later review decision. Changed referenced
catalog/source metadata fails closed. Values retain filename and physical source page.

The allowlist is local process configuration and `.env` is not loaded. This is OS
operator authorization, **not SaaS authentication**. SQLite file owners and arbitrary
trusted Python execution remain administrators; filesystem/source ACLs are required.
Legacy/unbound review audit records cannot grant authority without a protected event.
An SQL writer can add an adverse unbound review and deny access, but cannot grant it.
PostgreSQL authoritative writes fail closed until its production role adapter is tested.

```powershell
.\.venv\Scripts\python.exe -m sika.review identity
# Set only verified operator IDs in the trusted administrative shell; unset denies all.
$env:SIKA_REVIEWER_IDS = 'windows:<verified-SID>'
.\.venv\Scripts\python.exe -m sika.review approve --target data/processed/sika_v2.db --legacy data/processed/sika.db --release-id 3 --quality-run-id <latest-id> --reason '<review reason>' --evidence <reviewed.json>
.\.venv\Scripts\python.exe -m sika.review publish --target data/processed/sika_v2.db --legacy data/processed/sika.db --release-id 3 --approval-id <review-decision-id> --reason '<publication reason>'
```

Review JSON is handwritten from actual source inspection, never generated as a PASS
workaround. Its shape (synthetic placeholders, not an approval of the real corpus):

```json
{
  "source_values": "Evidence of value, period, unit and page verification",
  "revision_marks": "Evidence for the stored revision marks",
  "reuse_rights": "Evidence for the registered reuse permission",
  "verified_observation_ids": [1, 2],
  "warnings": {"small_release": "Why this reviewed subset is intentionally small"}
}
```

CLI exits 0 on a completed authorized action and 1 on refusal/configuration failure.
`identity` only prints the OS identifier; it does not register or authorize anyone.
Upgrade refuses pre-existing unsupported protected states; downgrade refuses when
publication events exist. Rehearse rollback only on disposable/restored databases.

### S0.4.2 verification

The completed suite passes **256 tests**, without live provider calls: 56 publication
contracts, 14 additional adversarial regressions, five administrative CLI checks,
and the existing migration, quality, extraction and MVP regressions. Coverage includes
authorization denial, stale/failed QA, copied approvals, source changes, complete
cohort transitions, transaction rollback, forged evidence/statuses, replacement
collisions, retrograde review IDs and audit-preserving downgrade refusal. One existing
Starlette test-client deprecation warning remains; it is not a failed test.

The local target was backed up to the ignored
`data/processed/sika_v2_before_s04_2.db`; its table contents were checked against the
pre-upgrade database. Revision `0003_publication_gate` is now applied to
`data/processed/sika_v2.db`. All 12 old quality records remain unchanged. The
[S0.4.2 report](reports/S0_4_2_inflation_quality.json) records current validator
`trust-core-inflation-1.1` runs 13-16: 242 observations, four releases, zero technical
hard failures and 16 warnings. No source facts, rights, dates or revision marks were
fabricated to remove those warnings.

All 242 observations and all four releases remain draft; both review and publication
event tables are empty. Protected reads return no rows for these drafts, and a real
administrative publication attempt with no configured operator is refused without
changing state. An exact S0.3 replay still returns the original reconciliation report;
all historical rows/passages/audit and the legacy SHA-256 are unchanged. The legacy
validator's baseline remains 136 hard findings and eight warnings.

S0.4.2 is an independently verified local gate, not certification of the source values
and not a switch of the MVP endpoints onto V2. PostgreSQL SQL rendering is covered,
but live PostgreSQL operation and a least-privilege adapter are not certified.

### S0.4.3: Source concordance complete, publication certification pending

The [source work packet](S0_4_3_SOURCE_CERTIFICATION.md) records complete exact
source concordance for all 242 canonical records (zero discrepancies), their original
links, and all 76 quarantine originals. The separate
[quarantine registry](S0_4_3_QUARANTINE_REVIEW.md) proposes per-ID triage. The
read-only `sika.source_review` tool grants no approval, inserts no QA/review history
and never mutates metadata or states. All 242 records remain draft.

It uses 45 checksum-bound independent anchors plus 944 directly parsed IHPC source
cells, without moving narrative citations to different pages or modifying a value.
S0.4.3 must still resolve revision marks and applicable source rights/date evidence,
and obtain authenticated approval of justified cohorts. No
commercial permission is currently verified; unknown rights are not a prohibition.
The complete suite now passes 351 tests without provider calls; no QA history or
operator authorization was created by source inspection. Both DB hashes are unchanged.
The future S0.5 public query service must read only approved/published observations.
S0.4 stays in progress until its full publication definition of done is verified.
