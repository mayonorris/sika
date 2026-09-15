# Sika SaaS architecture

Status: accepted direction for incremental implementation. This document describes
the target and migration boundaries; it is not a commitment to replace every MVP
component at once.

## Product boundary

Sika is a provenance-first economic data workspace. Official figures are discovered,
extracted, reviewed, published as canonical statistical series, queried through safe
application services, and reused in cited answers, exports, alerts, and briefs.

The platform monetizes workflow and service: saved work, monitoring, collaboration,
API usage, private sources, and service levels. Public official facts remain
discoverable with attribution.

## Architecture principles

1. **The database is the numeric authority.** A language model may interpret a
   question or explain retrieved rows, but it never invents or directly persists a
   statistical value.
2. **Published is a state, not the result of extraction.** Extracted data enters a
   review workflow and is queryable by end users only after hard validation passes.
3. **Series identity is stable.** Labels may change between publications; a canonical
   series identifier and explicit dimensions determine whether observations belong
   together.
4. **The server owns queries.** Models return typed intent. Application code resolves
   it against an allowlisted catalog and compiles bounded SQL.
5. **Tenant boundaries are explicit.** Every user-owned resource belongs to an
   organization and is protected by authorization at the service and database layers.
6. **Start as a modular monolith.** Deploy one application and separate workers while
   maintaining clear module boundaries. Split services only when measured operational
   pressure justifies it.
7. **Migrate without breaking the MVP.** New paths run beside legacy paths until data
   and golden-question parity are verified.

## Current and target state

| Concern | MVP today | Target |
|---|---|---|
| Statistical store | Committed SQLite database | PostgreSQL with migrations, constraints, indexes, and backups |
| Source files | Local data/raw/ | Versioned object storage with checksums and immutable releases |
| Ingestion | Local synchronous scripts | Idempotent jobs with source/page checkpoints and bounded retries |
| Publication | Confidence threshold at query time | Draft, reviewed, published, quarantined, and corrected states |
| Querying | Deterministic fallback plus legacy LLM-authored SQL | Typed intent resolved by a server-owned query compiler |
| Identity | Anonymous | Users, organizations, memberships, and role-based access |
| Product state | Browser session | Persistent workspaces, saved series, charts, briefs, and watchlists |
| Usage | Not metered | Organization-level events, quotas, entitlements, and cost attribution |
| Operations | Single Render web service | Web plus worker/scheduler, health checks, telemetry, and tested restore |

## Target modules

The first production architecture remains one FastAPI codebase with explicit modules:

- **catalog** — indicators, dimensions, canonical series, coverage, and search;
- **releases** — sources, documents, publication vintages, checksums, and licences;
- **ingestion** — deterministic parsers, assisted mappings, checkpoints, and jobs;
- **quality** — validation rules, review decisions, quarantine, and corrections;
- **query** — typed intents, catalog resolution, transformations, and citations;
- **identity** — users, organizations, memberships, sessions, and roles;
- **workspace** — saved series, charts, questions, briefs, and watchlists;
- **notifications** — release events, alert preferences, email, and later webhooks;
- **entitlements** — plans, usage counters, quotas, and billing-provider mapping;
- **operations** — health, audit events, structured logs, metrics, and job status.

Modules may share one PostgreSQL instance initially, but they must not reach into each
other's tables from route handlers. Service functions own cross-module operations.

## Trust Core data model

The first migration introduces these concepts before adding accounts or alerts:

- concepts: stable economic concept and canonical multilingual labels;
- geographies: canonical code, display labels, hierarchy, and aliases;
- units: canonical unit, scale, symbol, and allowed conversions;
- series: unique dimensional identity for one time series;
- sources: publisher, licence, canonical URL, and reuse status;
- releases: one immutable publication/vintage with checksum and publication date;
- observations: series, period, value, release, revision status, and provenance;
- passages: document fragments used only for context and verification;
- quality_runs: validator version, result, timestamp, and blocking findings;
- review_decisions: reviewer, decision, reason, and audit timestamp.

At minimum, a series identity includes concept, geography, frequency, unit, category or
sector, aggregation level, price basis, seasonal adjustment, and other source-specific
dimensions required to prevent false deduplication.

### Publication lifecycle

~~~text
discovered -> extracting -> draft -> validating -> reviewed -> published
                                  |                 |
                                  +-> quarantined <-+

published -> superseded or corrected
~~~

Only published observations may be returned by public product endpoints. Corrections
append an auditable decision; they do not silently erase the previous release.

## Primary data flow

~~~text
Official source
  -> source registry and licence check
  -> immutable document release
  -> deterministic/source-specific extraction
  -> staging observations and passages
  -> validation and reconciliation
  -> human review when required
  -> published canonical series
  -> query API, exports, alerts, and briefs
~~~

## Query safety boundary

The future /v1/query contract accepts structured fields such as series identifiers,
geographies, periods, comparison, aggregation, and output format. The natural-language
layer may propose this structure, but the backend must:

- validate every field against the catalog;
- authorize organization-scoped resources;
- compile parameterized SQL from known templates;
- apply row, period, execution-time, and result-size limits;
- return citations directly from selected observation provenance;
- record latency, result count, fallback reason, and cost attribution.

The legacy LLM SQL route remains only until the safe compiler reaches golden-question
parity. It must not gain new product capabilities.

## SaaS and operational boundary

- PostgreSQL is the system of record for product and statistical metadata.
- Object storage keeps immutable source documents and generated exports.
- Background jobs perform ingestion, validation, exports, scheduled briefs, and alerts.
- A scheduler discovers releases and creates idempotent jobs.
- Redis is optional and introduced only for measured cache, quota, or queue needs.
- Development, staging, and production use separate data and secrets.
- Schema changes use reviewed migrations; production startup never creates tables ad hoc.
- Backups have retention objectives and a periodically tested restore procedure.
- Health endpoints distinguish liveness, readiness, database access, and worker health.
- Logs are structured and carry request, organization, user, and job identifiers without
  storing prompts or source text unnecessarily.

## Security and privacy baseline

- Default-deny authorization for organization resources.
- Short-lived authenticated sessions, secure cookies, CSRF protection where applicable,
  and rate limits on public and authenticated routes.
- Secrets only in the deployment secret store; never in repository or logs.
- Audit events for sign-in, membership, export, API key, review, and correction actions.
- Explicit data retention for accounts, prompts, exports, and audit records.
- Source licence status must allow the requested exposure or redistribution mode.

## Migration sequence

1. **S0 — Trust Core:** canonical schema, data migration, validation gate, review state,
   and safe query compiler for inflation.
2. **S1 — Inflation Watch:** catalog pages, authentication, organization workspace,
   saved series, CSV export, and release watchlists.
3. **S2 — Paid pilot:** alerts, scheduled briefs, usage metering, entitlements, support
   tooling, and manual invoicing integration.
4. **S3 — Regional scale:** source scheduler, API keys, webhooks, additional countries,
   connectors, SSO, and contractual service levels.

## Explicitly deferred

- Forecasting or model-generated official statistics.
- Native mobile applications.
- Microservices or Kubernetes.
- A wholesale frontend rewrite before the catalog and workspace contracts stabilize.
- Expansion to all countries before freshness and retention are proven on the pilot.
- Coupling authorization directly to one payment provider.

## Architecture decisions still required

Each decision below gets an ADR before implementation:

1. PostgreSQL hosting and migration tooling.
2. Authentication/session provider versus first-party implementation.
3. Background job and scheduler implementation.
4. Object storage provider and document access policy.
5. Frontend migration path after S1 contracts stabilize.
6. Billing provider abstraction and local payment collection.

## S0 exit criteria

Trust Core is complete when:

- the inflation domain runs on stable canonical series identifiers;
- the migrated corpus has zero hard validation failures;
- only reviewed and published observations reach public endpoints;
- every returned number retains document and page provenance;
- the safe query compiler passes the relevant golden questions without model-authored SQL;
- migration rollback and restore procedures have been exercised in a non-production
  environment.
