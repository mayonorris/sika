# Trust Core data specification, version 2

Status: implemented by migration `0001_trust_core` (S0.2). The MVP continues to use
the legacy [DATA_SPEC.md](DATA_SPEC.md) and `data/processed/sika.db`. No real data is
imported and no public endpoint reads Trust Core in this ticket. S0.3 migrates the
inflation corpus; S0.4 enforces validation and review; S0.5 connects safe queries.

Decision: [ADR 0001](adr/0001-use-alembic-migrations.md). Migrations own the schema;
application startup does not create tables. This specification supersedes the legacy
logical identity rules only for the new database.

## Canonical identity

A series has a stable, unique `series_key`. Labels are display metadata and never
participate in deduplication. A second, database-enforced identity is:

`(concept_id, geography_id, frequency, unit_id, dimensions_id)`

`dimensions_id` references an immutable bundle with a unique, non-null tuple:

`(category, sector, aggregation_level, price_basis, seasonal_adjustment, source_variant)`

All six codes are explicit and nonempty; none has a default. For example, a national
all-items annual inflation rate observed monthly could use:

| Field | Illustrative code |
|---|---|
| concept | inflation_rate_yoy |
| geography | TG |
| frequency | monthly |
| unit | percent |
| category | all_items |
| sector | all_sectors |
| aggregation_level | total |
| price_basis | not_applicable |
| seasonal_adjustment | unadjusted |
| source_variant | none |

These are examples, not seeded mappings or claims about the current corpus. Catalog
codes are lowercase, stable, reviewed identifiers. `source_variant` is `none` only
when no additional distinguishing dimension exists. Otherwise it is a namespaced,
reviewed code describing the complete combination (methodology, population, coverage,
etc.), explained in `dimensions.description`. Different base years use distinct
price-basis codes; different source methodologies use distinct source variants.

Unknown scope is not equivalent to `all_items`, `total`, or `not_applicable`. S0.3 must
quarantine ambiguous mappings rather than guess a code. A changed identity requires
a new series/bundle; database triggers prevent updates to existing identities.

## Tables

All surrogate IDs are integer primary keys. Stable codes/keys are unique within their
catalog. Foreign keys use RESTRICT so referenced records cannot disappear silently.
Datetime inputs must be UTC; PostgreSQL stores timezone-aware timestamps, while SQLite
stores their UTC representation without an offset and is used for local tests only.

| Table | Contract |
|---|---|
| concepts | Stable code, required French canonical label, optional English label and definition. |
| geographies | Stable code, French/English labels, optional parent FK. Codes use an explicit namespace when not ISO country codes. |
| units | Code, labels, symbol, quantity and strictly positive decimal scale. Scale is metadata, not permission to convert unlike quantities. |
| dimensions | Six required codes forming a unique immutable bundle; optional description of source variants. |
| series | Stable key, five-part identity, labels, frequency in annual/quarterly/monthly/daily/irregular. |
| sources | Publisher registry, title, canonical URL, license URL/attribution, reuse status unknown/allowed/restricted/prohibited; default unknown. |
| releases | Stable vintage key, source FK, exact source filename, source URL and publisher snapshots, SHA-256, publication date (nullable if unknown), retrieval timestamp, immutable storage locator/media type, optional superseded release FK, lifecycle status. |
| observations | Series/release FKs, normalized period, decimal value, page/sheet index, optional precise locator, original printed label, nullable extraction confidence, revision status and publication state. |
| legacy_observation_links | Composite key (legacy_database, legacy_id), observation FK and complete original row as JSON. Many legacy rows can map to one canonical observation only after an explained reconciliation. |
| passages | Release FK, page/sheet index and contextual text; unique (release_id, source_page). Optional paired legacy_database/legacy_id with unique mapping. Never a numeric authority. |
| quality_runs | Release FK, validator version, running/passed/failed state, UTC start/end, nonnegative failure/warning counts, structured findings report. Passed requires zero hard failures and completion time. |
| review_decisions | Exactly one target (release or observation), reviewer reference, decision, nonempty reason and audit timestamp. Append-only, including before identity accounts are introduced. |

## Observations and provenance

- Logical uniqueness is `(series_id, period, release_id)`. Two publications may report
  the same figure. A conflicting value in one publication must be reviewed; confidence
  does not authorize overwriting it or creating a second canonical observation.
- `value` is NUMERIC(28,10), with no missing-value sentinel. PostgreSQL preserves that
  decimal precision. SQLite numeric affinity may use floating-point storage: local
  tests do not certify arbitrary-precision financial calculations.
- Existing annual, quarterly and monthly normalization rules remain `YYYY`, `YYYY-QN`
  and `YYYY-MM`. Daily uses `YYYY-MM-DD`; irregular periods require a documented mapping.
  S0.4 validates period syntax, calendar validity and compatibility with frequency.
- `source_page` is required and >= 1. PDF citations use the physical 1-based page;
  Excel uses the 1-based sheet index plus `source_locator` for sheet name/cell range.
- The exact filename comes from the release; `original_label` preserves source wording.
  Confidence is nullable or in [0,1]; it is extraction confidence, never uncertainty.
- Revision status is unknown/provisional/revised/final. Preserve source revision marks;
  the legacy simplification that discarded them does not apply to new ingestion.
- A release's identity and provenance cannot be updated, and releases cannot be deleted.
  An erroneous date or locator is corrected by a new release linked with `supersedes_id`.
  Identical document bytes may occur in two releases, so checksum is indexed but not unique.
  Re-ingestion idempotency uses the stable `release_key`.
- Identity changes and corrections must preserve original rows. S0.3 records the source
  database snapshot identity in `legacy_database` (including its checksum), the old
  observation ID, and every original field in `original_row`. This ticket provides
  the mapping storage only; it does not perform or claim corpus reconciliation.

## Publication boundary

Releases and observations default to `draft`. Release states are discovered, extracting,
draft, validating, reviewed, published, quarantined, corrected, superseded. Observation
states are draft, reviewed, published, quarantined, corrected, superseded.

S0.2 constrains state vocabulary and stores audit evidence; it does not authorize
transitions. S0.4 must enforce the quality/review gate, observation correction rules,
license checks, authenticated reviewers, and published-only reads. Do not connect this
schema directly to public endpoints before that gate. Private sources and user-owned
resources require organization boundaries in a later ticket; these tables are for the
shared official statistical catalog.

## Indexes and migration checks

Unique indexes serve catalog keys, complete identities, vintage uniqueness and legacy
lookup. Secondary indexes serve series by geography/unit/dimension bundle, published
series-period reads, release/page provenance, source filename/checksum discovery,
publication chronology, geographic parents, supersession, quality history and reviews.

The dedicated test suite exercises populated upgrade/downgrade/upgrade, duplicate and
NULL-identity rejection, FK enforcement, immutable release provenance, append-only
reviews (including SQLite REPLACE attempts), legacy field round-trip, draft defaults
and transactional SQLite DDL rollback.
It also compiles PostgreSQL upgrade/downgrade SQL without a network connection. A live
PostgreSQL migration test remains required before deploying this schema on PostgreSQL.

## Local migration commands

Run from the repository root after installing requirements. Set a dedicated database
URL explicitly in the shell; the migration command does not load .env or use SIKA_DB.

```powershell
$env:SIKA_DATABASE_URL = 'sqlite:///data/processed/sika_v2.db'
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m pytest tests/test_schema_migrations.py -q
```

`sika_v2.db` is ignored by Git and is not the MVP database. The environment refuses an
existing database with tables but no applied Alembic version. Do not bypass that guard
with `stamp` on a legacy database.

Rollback deletes all Trust Core tables and their data. Use only on a disposable database
or after taking and checking a backup of a populated environment:

```powershell
.\.venv\Scripts\python.exe -m alembic downgrade base
```

Tests use temporary paths and leave the committed legacy database untouched.
