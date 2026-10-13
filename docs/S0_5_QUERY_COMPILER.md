# S0.5.1 — Typed, bounded query preparation

Status: implemented contract/compiler; validation results are recorded in the active
backlog. This preparatory brick does not complete S0.5 or expose V2 data.

## Compatibility and dependency exception

The maintainer approved preparing S0.5.1 on synthetic fixtures while S0.4.3 remains
open for reuse terms, publication-date/revision evidence and genuine operator review.
The completed source concordance is not publication authority. Neither the MVP API/UI
nor either real database, lifecycle state, schema or source metadata is changed here.
No provider call is made and no dependency is added.

The decision is recorded in [ADR 0004](adr/0004-prepare-bounded-inflation-queries.md).

## Contract version 1

`sika/query.py` accepts a strict `QueryIntent`. Unknown fields, coercion from strings
or booleans, unsupported operations and constructed/tampered model instances are
validated again at the public boundary. Validation errors carry a stable `QueryError`
code and a generic message, without echoing the submitted value. Validation exception
chains are suppressed in formatted tracebacks; callers must also avoid logging the
original request or inspecting/logging the internal exception context.

Example of structured intent, not a natural-language parser:

```json
{
  "version": 1,
  "operation": "series",
  "selector": {
    "concept": "inflation_rate_yoy",
    "geography": "TG",
    "frequency": "monthly",
    "unit": "percent",
    "category": "all_items",
    "sector": "all_sectors",
    "aggregation_level": "total",
    "price_basis": "not_applicable",
    "seasonal_adjustment": "not_reported",
    "source_variant": "test:method_a"
  },
  "period_start": "2023-01",
  "period_end": "2026-06",
  "limit": 100
}
```

`test:method_a` identifies a synthetic fixture, not an official methodology.

- `selector` is either an exact `series_key` alone or concept/geography aliases with
  optional explicit identity filters. Mixing those forms is rejected.
- Missing dimensions have no defaults. More than one complete identity is an
  `ambiguous_series` error with sorted server-owned candidate keys. There is no
  first-match, label-based or confidence-based selection.
- A complete identity is concept, geography, frequency, unit and all six dimensions:
  category, sector, aggregation level, price basis, seasonal adjustment, source variant.
- The server-owned `SeriesCatalog` rejects duplicate keys and duplicate full identities,
  invalid pilot concepts/geographies/units/category-level combinations and more than
  2,000 entries. Each entry permits at most 16 concept and 16 geography aliases.
- Aliases normalize case/accents using the existing reviewed mapping helper. They map
  globally to a concept/geography code, never to only the vintage declaring an alias.
  Collisions remain ambiguous; an alias cannot shadow another canonical code or its
  official label (for example, a Benin series cannot claim the alias `Togo`).
- The pilot supports the five reviewed inflation/CPI concepts, monthly frequency and
  quarterly year-on-year inflation, explicit CPI bases 2014/2023, all sectors and
  unreported seasonal adjustment. It does not infer unsupported identities.
- Both period bounds are mandatory and inclusive: `YYYY-MM` for monthly series and
  `YYYY-QN` for quarterly series. Year zero, invalid/mixed calendars, reversed ranges
  and spans exceeding 240 periods fail. No filename or current-date inference is used.
- `limit` is a strict integer from 1 to 500; the default is 100.

The stable error codes are `invalid_intent`, `invalid_catalog`, `unknown_series`,
`ambiguous_series`, `invalid_period` and `period_range_too_large`.

## Compiler and publication boundary

`compile_query(intent, catalog)` returns an immutable `PreparedQuery`: fixed SQL,
bound parameters, resolved metadata, requested row limit and internal fetch limit.
It has no execute method and `execution_authorized` is always false. Importing or
calling this module does not read `.env`, a database, a source archive or a provider.
The catalog is identity metadata only; possessing a key never authorizes its data.
Parameters are an immutable mapping; a driver requiring a concrete dictionary must
receive a copy, `dict(plan.parameters)`, only inside its separately authorized adapter.

The template selects only from **`authorized_observations`**, a required future adapter
relation. It does not exist in the production schema and this brick does not create a
view, migration or executable service. Tests create it solely in an in-memory SQLite
fixture. It must never be replaced with a raw `observations` query or a status filter.

Before any public execution, a future service must obtain fresh snapshot-bound
`sika.publication.published_rows()` proof for every contributing release, including
the current authorization, QA/review and artifact checks. It must build/use the
authorized relation from those proof-checked rows without a stale cache or a second
unprotected read. Compilation and synthetic tests grant no publication authority.

The SQL binds the exact series key, start/end periods and fetch limit. Relation,
column names, predicates and order cannot come from the request. Selected provenance
is `release_id`, `source_doc`, `source_page`, `source_url`, `source_locator` and
`revision_status`, alongside observation identity, period, value and unit.

Rows are ordered by period, release ID and observation ID. **All publication vintages
are retained**. No latest-vintage policy, confidence deduplication, aggregation,
comparison, unit conversion or forecast is implemented. A period with no observation
produces no invented value.

The compiler fetches `limit + 1` rows (at most 501) as an internal truncation sentinel.
A future result adapter must remove the extra row, disclose `has_more`/truncation and
never present a capped set as a complete chart. This brick supplies no result adapter,
pagination, execution timeout or response-size enforcement; those are execution duties.
PostgreSQL runtime support is not certified by the in-memory SQLite tests.

## Verification and next brick

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_query.py -q
cmd /C "set OPENAI_API_KEY=test&& .venv\Scripts\python.exe -m pytest -q"
```

The focused suite covers strict validation and forged instances, aliases/ambiguity,
complete identities, injection attempts, calendar/range boundaries, immutable plans,
sentinel behavior and provenance/vintages in an in-memory fixture. Its structured
equivalent of golden question 1 is not proof that a new natural-language endpoint is
delivered. Existing MVP golden-question regressions remain on the unchanged API.

S0.5.2 should implement a default-deny, proof-bound execution and result service on
synthetic publication fixtures first: catalog loading, fresh per-release proof,
execution/resource limits, citation serialization, truncation handling and invalidated
proof tests. API integration and real inflation answers remain gated on the open
S0.4.3 evidence and on end-to-end golden-question parity. S1 is still gated on S0 exit.
