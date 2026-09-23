# ADR 0002: Reconcile complete inflation snapshots before publication

Status: accepted for S0.3 within the Trust Core migration boundary.

## Context

Legacy indicator keys mix headline, category and secondary-group figures. Labels and
confidence cannot identify a series safely. Some records are forecasts, retail-price
changes or weights, while others lack a defined measurement window. Dropping them or
assigning a guessed identity would break the provenance contract.

## Decision

Use a deterministic, source-specific mapping registry bound to reviewed PDF SHA-256
checksums. Keep concept, geography, frequency, unit and all six dimensions explicit.
Preserve the original value and every original field. Do not call a model provider.

Migrate one checkpointed SQLite snapshot into an empty, explicitly named Trust Core
database. Read the legacy database in read-only mode; verify its checksum before and
after the import. Record all selected observation and passage IDs in dedicated audit
tables, including unresolved records that cannot reference a canonical observation.

Merge only equal values with identical series, period and release. Keep all original
citations through legacy links; choose the primary citation by physical page then
legacy ID. Quarantine every candidate of a conflicting value group. Do not use
confidence to choose a numeric truth.

Write the whole snapshot transactionally. Before commit, independently read persisted
rows and reconcile values, identities, citations, original JSON and ID coverage. An
exact replay verifies the same records without adding rows. A changed snapshot,
mapping version or source manifest requires another target; this is not a general
incremental ingestion interface.

Keep verified source bytes in a checksummed local archive beside the database.
Archive copies are outside the SQL transaction and may remain after a failed import;
they are not served or committed. They are not a substitute for future production
object storage. Unknown publication dates and reuse rights remain unknown.

## Consequences

- The draft store has complete accounting even when a canonical identity is unresolved.
- Quarantine is a migration disposition, not an approval decision or a discarded row.
- A reconciliation PASS does not certify the original extraction or permit publication.
- No API/UI, live provider, deployment, authentication or source-ingestion change is
  included. S0.4 owns review/publication; S0.5 owns safe public queries.
- Back up the canonical database and its sibling source archive together. SQLite
  migration tests do not replace live PostgreSQL deployment tests.
- Downgrading only revision 0002 removes audit evidence but leaves 0001 data. Do not
  use that as an import undo. Rebuild only a disposable target through `downgrade base`
  and `upgrade head`; the legacy snapshot and source files remain intact.
