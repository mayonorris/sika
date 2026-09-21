# ADR 0001: Version the Trust Core schema with Alembic

Status: accepted for S0.2.

## Context

The MVP creates SQLite tables inside extraction scripts. Trust Core needs explicit,
reversible migrations and database constraints before the inflation migration (S0.3).
PostgreSQL is the target, while local schema tests must work without a running server.

## Decision

Use SQLAlchemy 2 Core types and Alembic migrations. SQLite is the local migration-test
backend; PostgreSQL is the production target. Hosting, the PostgreSQL driver and live
PostgreSQL integration are deferred to their deployment decision. SQL generation for
PostgreSQL is tested, but is not a substitute for running migrations on that server.

Use a separate Trust Core database, selected explicitly by SIKA_DATABASE_URL. Do not
load .env implicitly, use SIKA_DB, or run migrations at application startup. Reject
unversioned databases that already contain tables, including the legacy database.

Model additional statistical dimensions as immutable bundles of six required codes:
category, sector, aggregation level, price basis, seasonal adjustment, source variant.
A composite unique constraint identifies each bundle. A second composite constraint
identifies a series by concept, geography, frequency, unit and dimension bundle. No
identity column is nullable. Source-specific combinations use a reviewed, namespaced
source-variant code; their meaning must be documented before ingestion.

Make release provenance immutable with dialect-specific database triggers. Corrections
create a new release linked to the previous release. Keep legacy row mappings and
original labels alongside canonical identities; never resolve an ambiguous mapping by
dropping a legacy identifier. New records default to draft, and the publication gate
will be implemented in S0.4.

## Consequences

- Two development dependencies are also installed on deployments via requirements.txt.
- Schema changes ship as reviewed migrations with upgrade and downgrade paths.
- SQLite connections must enable foreign keys and recursive triggers (including
  delete guards on REPLACE); the shared engine factory enforces both.
- Rollback drops Trust Core tables and their contents; test it only on disposable
  databases and back up populated environments first.
- S0.2 adds the storage contract only. It does not route MVP queries to this database,
  import the corpus, enforce review transitions, or add private tenant-owned data.

References: [Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
and [SQLAlchemy constraints](https://docs.sqlalchemy.org/en/20/core/constraints.html).
