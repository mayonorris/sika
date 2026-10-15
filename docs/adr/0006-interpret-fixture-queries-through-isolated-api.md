# ADR 0006: Interpret fixture questions through an isolated, protected query API

Status: accepted for S0.5.3 and synthetic publication fixtures only.
Date: 2026-10-15
Decider: project maintainer through the approved S0.5.3 implementation directive.

## Context

[ADR 0004](0004-prepare-bounded-inflation-queries.md) established a strict intent,
canonical resolver and fixed SQL compiler. [ADR 0005](0005-execute-queries-from-protected-snapshots.md)
added local execution from a held, freshly checked publication snapshot. Neither a
compiled plan nor a matching catalog identity grants access to observations.

The next increment connects natural-language questions to that service through an
HTTP contract. It must prove the complete path on genuine synthetic publication
fixtures without migrating the working MVP, enabling real draft observations or
introducing model-authored SQL. S0.4.3 remains open for applicable reuse terms, exact
publication dates, revision evidence and genuine operator review. The maintainer
approved extending the synthetic-only sequencing exception to this integration.

The language and transport layers introduce new risks: silently ignoring meaningful
words, selecting an arbitrary source variant, accepting caller-owned paths or proof,
unbounded request bodies, concurrent verification work and returning a truncated
series as complete. HTTP cancellation also does not terminate a synchronous source
parser. A timeout must not release the worker's capacity slot while it still holds
the protected SQLite snapshot.

## Decision

Add a deterministic interpretation module and a separate `POST /v1/query` router.
The legacy application registers the router before its catch-all static mount, but
supplies no fixture configuration: the new route is disabled with a sanitized 503
response. Its presence is not a real-data API migration. `/ask`, `/brief`, the UI,
the schema, both real databases and publication states remain unchanged.

### Closed language-to-intent contract

Interpret the entire normalized question with bounded, full-match French and English
templates. There is no model call, external language service, database lookup or SQL
generation in interpretation. Unknown words and unsupported modifiers are not
discarded. Missing geography or period requires clarification; unsupported domains,
forecasts, comparisons, aggregation, negation, conflicting rate windows, latest-value
requests and multiple geographies are refused rather than approximated.

Support the five reviewed inflation-pilot concepts: CPI index, year-on-year inflation,
month-on-month inflation, three-month price change and twelve-month average inflation.
Explicit supported wording distinguishes their rate windows. Bare "inflation" means
headline year-on-year inflation, not an inferred average or monthly change. The
headline selection uses all items, all sectors and total aggregation, with the pilot's
reported seasonal-adjustment convention. Record these interpretation assumptions in
the response; do not silently select a source variant or CPI index basis.

Require an explicit supported geography and an explicit year, month, quarter or
inclusive range. A year expands to monthly January through December, not an annual
average. Monthly dates retain monthly frequency; explicit quarters select quarterly
year-on-year inflation only. "Since" ends at the trusted server clock's current month
or quarter and discloses this expansion. Do not use a filename, publication label,
latest observed row or caller-supplied clock as a temporal authority. Reject invalid
calendars, mixed frequencies, reversed ranges and requests above 240 periods.

The request contains only version, question, row limit, language and optional explicit
`source_variant` / `price_basis` filters. Those filters disambiguate identity; they
grant no publication authority. A supported CPI basis may also be stated in the
question; conflicting explicit bases are refused. Unknown fields, type coercion and
constructed/tampered model instances must not bypass validation. The output is the
existing strict `QueryIntent`, not a SQL string or pre-authorized plan.

### Default-disabled, fixture-confined HTTP integration

`build_query_router(settings=None)` is closed by default. Trusted fixture applications
may inject `QueryFixtureSettings` with an explicit fixture root, target, optional
source/legacy paths, lowerable execution limits and a trusted clock. No `.env` setting,
request field or public switch enables the route or supplies these values.

Configured paths must resolve inside the fixture root. Refuse the two known real
database targets, their aliases, and paths outside that root. A root declaration is a
trusted test-application boundary, not an automated certificate that arbitrary
administrator-controlled files are synthetic. Do not add a fixture-data loader or
schema initializer to production startup. Existing publication proof remains
mandatory even inside a configured fixture application.

Validate a strict JSON object after enforcing the 8 KiB streamed-body ceiling on
actual bytes, independently of `Content-Length`. Bound body reading to two seconds.
Check media type/charset, valid UTF-8 and JSON; reject duplicate keys, non-finite
numbers and unknown fields. Invalid or unsupported input must fail before database
execution. Request bodies cannot contain SQL, settings, paths, catalogs, sessions,
rows or publication proofs. Expected errors are bilingual, structured and sanitized;
do not echo question text, local paths or underlying exceptions in replies or logs.

### Fresh publication proof and exact result transport

Call `execute_query()` afresh for each successfully interpreted request. Reuse its
strict intent validation, protected snapshot, complete contributing-cohort checks,
isolated in-memory SQL, deadline/resource budgets and final artifact/authorization
checks. Do not add a cached catalog, cached proof, raw-observation read or lifecycle
status shortcut. Unproved ambiguity candidates are not a public discovery result.

Return decimal values as strings with exact row-level citations and revision/release
identity. Preserve every selected vintage and explicit `has_more`, `complete` and
truncation warnings. A deterministic explanation may summarize the retrieved result,
but it must not manufacture values, collapse vintages or imply that "not truncated"
means gap-free source coverage. Empty and failed results contain no invented numeric
observations; a publication refusal returns no partial trustworthy subset.

Serialize the final HTTP envelope using compact UTF-8 and enforce its response-byte
ceiling after interpretation metadata and explanation have been added. The lower
service's size check alone does not bound a larger HTTP wrapper. Do not silently
truncate provenance fields to fit. A chart adapter is not included: the API preserves
the metadata a later vintage-aware consumer needs without presenting an unqualified
chart as correct.

### Capacity belongs to the physical worker

Use one dedicated execution thread per configured fixture router and a nonqueueing
admission gate. A busy router returns 429 with `Retry-After` rather than accumulating
source-verification work. The physical worker owns its capacity slot until it has
actually completed and cleaned up the protected read, including after an HTTP
timeout, cancellation or disconnect. Shield the submitted future from cancellation
and retain ownership until real completion; do not treat cancellation of an awaiter
as termination of the parser.

The HTTP wait budget is six seconds; the existing service deadline is at most five
seconds and remains cooperative. The HTTP timeout can stop waiting but cannot kill a
Python/PDF/filesystem operation. This is a bounded local fixture integration, not hard
worker isolation, a distributed rate limit, a latency guarantee or production load
certification. Authentication, tenant authorization and production deployment are
not established by the absence of user-owned resources in these fixture requests.

## Options considered

| Choice | Benefit | Limitation |
|---|---|---|
| LLM intent router | Broader phrasing with less grammar maintenance | Provider calls, latency and another fallible interpretation boundary; typed validation and complete semantic checks would still be required |
| Closed deterministic grammar | Reproducible, local, no model cost, explicit unsupported scope | Deliberately narrow vocabulary; new supported formulations need tests and review |
| Environment-activated real-data route | Simple deployment wiring | Can accidentally expose uncertified data or confuse synthetic verification with real publication readiness |
| Default-disabled router with trusted fixture injection | Exercises the HTTP path without changing the live MVP data boundary | Test applications must explicitly supply and protect fixture configuration |
| Release a slot when the HTTP task times out | Appears responsive and simple | A still-running parser can retain its snapshot while replacement work accumulates |
| Retain the slot until the worker physically ends | Bounds actual concurrent verification work | A stuck cooperative parser can keep the fixture route unavailable until process intervention |

## Trade-off analysis

Choose the deterministic grammar, default-disabled fixture injection and physical
worker lifetime ownership. The priority of this brick is reproducible semantic and
publication safety, not maximum linguistic coverage or throughput. A refusal that
asks for a supported formulation is preferable to an apparently fluent answer to a
different question.

Fixture confinement and a disabled default are complementary to publication proof,
not replacements. Trusted Python application code and filesystem administrators remain
privileged; request validation is the untrusted-client boundary. Local fixture tests
cannot establish commercial reuse permission or turn real draft cohorts into public
observations.

Retaining a worker slot on timeout intentionally favors bounded work over immediate
availability. Hard timeout recovery requires a separately managed parser process,
not an abandoned thread or a shorter HTTP timer. Production concurrency and distributed
admission policy must be measured and designed separately.

## Consequences

- Supported French and English inflation questions reach the existing strict compiler
  and fresh publication gate without executing model-authored SQL.
- Unsupported semantics and ambiguous identities refuse predictably rather than
  selecting an arbitrary matching row or dropping meaningful words.
- HTTP request/response bytes, body time and actual concurrent execution work have
  explicit local ceilings; cancellation does not imply parser termination.
- The new route is registered but unusable by default. No environment-only activation,
  production migration, provider dependency or UI change follows from this ADR.
- Preserved decimal values, citations, vintages and truncation remain suitable for
  later carefully scoped consumers, without claiming a chart/rendering implementation.
- S0.4.3, full S0.5 golden-question parity, the S0 exit gate and S1 remain open.

## Action items and verification

1. Implement the pure interpreter, thin fixture router and disabled registration;
   document the supported grammar, assumptions, HTTP envelopes and remaining limits.
2. Verify French/English structured equivalents of inflation golden question 1 through
   genuine temporary QA/review/publication fixtures. Test explicit dates and windows,
   missing fields, unsupported semantics and ambiguity before claiming language parity.
3. Verify strict malformed-body behavior, actual streamed-byte limits, timeouts,
   cancellation, nonqueueing contention and final encoded-response limits. Confirm
   slots remain occupied until timed-out physical work actually ends.
4. Exercise fresh proof revocation between requests, a broken contributing cohort,
   citation/decimal/vintage preservation, empty ranges and explicit truncation. Expected
   failures must expose neither numeric rows nor private input/configuration.
5. Run the legacy regression suite without live provider calls; recheck both real
   database hashes, disabled application defaults and unchanged `/ask`/`/brief`/UI.
   Record measured validation separately; this ADR does not assert a test PASS.
6. Before real enablement, finish S0.4.3 evidence and genuine approval; define authenticated
   authorization, hard parser worker isolation, immutable artifacts, workload budgets,
   operational telemetry, PostgreSQL runtime behavior and a reviewed activation path.
7. Add further reviewed grammar/golden questions and vintage-aware rendering in later
   increments. Do not retire the legacy path or mark S0/S1 gates complete based only
   on fixture integration.
