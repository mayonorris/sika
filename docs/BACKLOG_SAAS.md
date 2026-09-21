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

Status: queued

- Map IHPC/inflation rows to canonical concepts, dimensions, series, and releases.
- Preserve source document and page for every row.
- Produce a reconciliation report between legacy and migrated values.
- Quarantine ambiguous category or aggregate mappings.

DoD: all served inflation values reconcile exactly; no unexplained row is silently
dropped or merged.

### S0.4 Enforce the publication quality gate

Status: queued

- Separate staging observations from published observations.
- Run hard validation after every ingestion job.
- Block publication on invalid period, unit, series identity, missing provenance, or
  unresolved duplicate.
- Store quality runs and reviewer decisions.

DoD: invalid fixtures cannot become public; the migrated inflation corpus has zero hard
failures and an auditable approval.

### S0.5 Add the safe inflation query compiler

Status: queued

- Define a typed query-intent contract.
- Resolve aliases to canonical series identifiers.
- Compile parameterized, bounded SQL owned by the server.
- Return citations from observation provenance.
- Keep the current fallback for unsupported domains while migration continues.

DoD: inflation golden questions pass without executing model-authored SQL; malicious or
oversized intents are rejected predictably.

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
