# ADR 0004: Prepare bounded inflation queries without publication authority

Status: accepted for S0.5.1 preparation and synthetic verification only.
Date: 2026-10-13
Decider: project maintainer through the approved S0.5.1 implementation directive.

## Context

The target architecture forbids model-authored SQL. Its query layer must translate
validated intent into a canonical statistical identity and server-owned, bounded SQL.
The current MVP router and fallback remain in service on the separate legacy database.

S0.4.3 has completed source-value concordance and individual quarantine triage, but
reuse terms, exact publication dates, revision evidence and genuine authorized review
remain unresolved. Technical concordance is not publication approval. The real V2
inflation cohort is still draft and cannot become a public query source.

The maintainer approved preparing the isolated query contract while those external
dependencies remain open. This is an explicit sequencing exception to the top-to-bottom
S0 backlog: S0.5.1 may be implemented and tested against synthetic fixtures, but S0.4
is not declared complete and no real-data execution or endpoint migration follows
from this exception.

## Decision

Add a pure, non-executing query module in `sika/query.py`. It accepts typed intent and
an explicitly supplied, server-owned catalog, resolves a unique canonical series and
returns a prepared SQL statement with separate parameters. It does not load `.env`,
call a provider, discover a database, execute SQL or read a catalog from storage.
No new dependency, route, database migration or frontend change is part of S0.5.1.
The exact public contract and pilot vocabulary are documented in
[S0.5.1 query preparation](../S0_5_QUERY_COMPILER.md).

### Intent and resolution

- Support only the `series` operation in this increment. Require an explicit inclusive
  start and end period; accept normalized monthly `YYYY-MM` or quarterly `YYYY-QN`
  periods compatible with the resolved series frequency.
- Reject unknown fields, invalid types, coercion attempts, invalid calendar periods,
  reversed ranges and oversized requests. Version the intent contract explicitly.
  Revalidate supplied model instances so constructed or tampered objects cannot
  bypass validation at the public boundary.
- Accept an exact series key, or concept/geography aliases with optional explicit
  identity constraints. A series-key selector is exclusive of other selector fields.
- Resolve aliases against controlled catalog metadata, never against SQL identifiers
  or observation labels. Missing constraints do not silently mean `all_items`, `total`,
  a particular base year, adjustment method or source methodology.
  Reserve canonical codes and official labels against conflicting aliases.
- Match the complete canonical identity: concept, geography, frequency, unit and
  category, sector, aggregation level, price basis, seasonal adjustment and source
  variant. Refuse unknown and ambiguous selections predictably; never pick the first
  candidate, highest-confidence observation or an arbitrary label.
- Reject duplicate keys and duplicate complete identities in the supplied catalog.
  Catalog metadata identifies candidates; it is not proof of publication permission.

### SQL and result boundary

Use one fixed `SELECT` template against `authorized_observations`. This name is a
future service-owned execution relation, not a new Trust Core table or an unguarded
view over `observations`. S0.5.1 creates it only in synthetic in-memory test databases.
The caller cannot provide a table, selected columns, ordering expression, SQL fragment,
aggregation or transformation.

Bind the resolved series key, inclusive period bounds and fetch limit as parameters.
Allow at most 240 periods per request and a requested row limit from 1 through 500,
defaulting to 100. Fetch one additional sentinel row (`limit + 1`, at most 501) so a
future result consumer can detect truncation. That consumer must remove the sentinel
and report incomplete coverage; a bounded result is not evidence of a complete series.

Order by period, release ID and observation ID. Preserve every selected publication
vintage rather than silently choosing a latest or highest-confidence value. Select
observation identity, series identity, period, value, unit, release and revision
metadata together with exact source document, source page and source URL provenance.
Retain the precise source locator when available.
This keeps citations attached to the actual returned observation, not reconstructed
from an alias, filename convention or separate model response.

### Compilation is not authorization

A valid prepared query grants no execution or publication authority. The module has
no executor, its plan reports `execution_authorized: false`, and it does not access
either real SQLite database.

The later service must derive `authorized_observations` exclusively from a fresh
`sika.publication.published_rows()` protected read, with its snapshot-bound proof and
fail-closed behavior. A `status = 'published'` predicate, stored catalog entry, old
approval, successful compilation or previously materialized result is insufficient.
Construction and consumption of that relation must preserve the verified read's
consistency boundary; later integration must test stale evidence and concurrent
changes rather than reuse an unchecked persistent copy.

This does not implement natural-language interpretation, a public query endpoint,
tenant authorization, query-time telemetry or a production execution adapter. The
legacy API, UI, schema, real database files and all publication states remain unchanged.
Forecasts, conversion, aggregation, comparison, `latest` selection and vintage
collapsing require later explicit contracts. SQLite fixture verification does not
certify a live PostgreSQL runtime or deployment.

## Options considered

| Option | Benefit | Cost or limitation |
|---|---|---|
| Expand the legacy SQL router | Reuses the current question-to-SQL path | Retains model-authored SQL and cannot provide the target typed identity boundary |
| Add an ORM query builder with direct execution now | Can combine compilation and retrieval in one service | Expands scope into protected reads before source certification; an ORM alone does not establish authorization or resource limits |
| Typed resolver and fixed, non-executing SQL plan | Small, deterministic contract with explicit bounds and independently testable failure states | Delays end-user functionality; requires a later publication-aware executor and intent interpreter |

## Trade-off analysis

Choose the third option. A fixed template is sufficient for one series operation and
has fewer allowed behaviors to audit than a general-purpose query language. Typed
validation and complete identity resolution address semantic ambiguity; parameter
binding addresses SQL injection. Neither substitutes for the publication gate.

Separating preparation from execution permits useful progress without exposing draft
data or changing the working MVP. It intentionally leaves result consumption,
publication-proof freshness, execution-time limits and transport-size limits to the
next integration brick. No latency, throughput or production scalability claim follows
from in-memory fixture tests.

## Consequences

- Malformed or ambiguous intent is refused before any database work. Input ordering
  cannot determine which series is selected.
- Citations and release distinctions remain available without confidence-based
  deduplication. Consumers must not turn multiple vintages into one unqualified chart.
- The compiler is reusable by a future deterministic or LLM-assisted interpreter;
  any such interpreter remains outside the numeric and SQL authority boundaries.
- Synthetic integration tests can exercise filtering, ordering, provenance, bounds,
  the truncation sentinel and injection attempts without a provider or real database.
- S0.5.1 completion is preparatory only. S0.5 golden-question parity and the S0 exit
  criteria remain open until publication-aware execution and integration are verified.
- No publication permission, reviewer identity, source metadata or tenant authority
  may be fabricated to unblock subsequent work.

## Action items

1. Implement and verify the strict intent, canonical resolver and fixed SQL plan.
2. Exercise invalid types, ambiguous aliases, duplicate identities, malicious fields,
   invalid periods, oversized ranges and limits using synthetic tests.
3. Execute prepared plans only on a synthetic in-memory relation; verify retained
   vintages, exact citations, deterministic ordering and `limit + 1` behavior.
4. Preserve existing regressions and both real database hashes; document this
   sequencing exception and the remaining S0.4/S0.5 boundaries in the active backlog.
5. Design publication-aware execution with fresh protected proof, consistency checks,
   structured truncation/empty states, time limits and result-size limits before
   connecting real data or public endpoints.
6. Complete source certification and genuine authorized review; verify the relevant
   inflation golden questions before retiring the legacy SQL path.
