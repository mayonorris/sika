# S0.4.3: Source certification work packet

Status: **source concordance and technical implementation complete; publication
certification blocked on external evidence and genuine operator review**. Source
inspection took place on 2026-10-11 and 2026-10-12. All 242 canonical observations
have matching source evidence; all 76 quarantines have individual triage. The
remaining rights/date/revision decisions and auditable operator approval are not
invented to close the ticket. No actual observation is approved or published.

## Implemented boundary

`python -m sika.source_review` requires an explicit existing SQLite Trust Core target
at revision `0003_publication_gate`. It opens it with `mode=ro`, `query_only=ON` and
an explicit read transaction. The MVP database is rejected as a target. It does not
load `.env`, resolve/allowlist a reviewer, call a model/provider, create QA history,
modify catalog metadata, update observations, or create review/publication events.

The packet includes every canonical observation with its complete series identity,
original label, source filename, physical page, value, period and legacy IDs. Every
legacy observation link and each original quarantine are retained with a canonical
JSON SHA-256. Orphan observations remain visible rather than disappearing in joins.
The existing technical checker independently checks provenance and reconciliation;
the exact read-only legacy snapshot is required for migrated records.

The source archive is bounded by `--source-root` (default: the target's sibling
`trust_core_sources/`). Source hashes/page counts and the legacy checksum are checked
again after comparison. A changed artifact is a hard failure, not a matched anchor.
The report exporter refuses database/source overwrite, including `.json` hardlink
aliases. No full-page PDF text, credential or model prompt is exported.

`sika/source_facts.py` holds **45 selected comparison anchors** transcribed from
complete inspected PDF pages, independently bound to literal original SHA-256s.
They compare the exact value, observation period, physical page, geography, concept,
unit, frequency and all six dimensions. A matching number somewhere on a page is
insufficient. Changing a parser profile does not transfer anchors to different bytes.
Missing/duplicate/mismatching anchors fail; figures are never repaired or rounded.
These include ten explicitly inspected narrative anchors: eight June division
rates on physical page 1, and May's two narrative measures on physical page 4.
The original prose citations are preserved, not moved to a continuation table.

`sika/source_tables.py` independently parses **944 supported printed cells** from
the two exact IHPC PDFs. It derives periods from table headers, preserves Decimal
values and all identity fields, and excludes weights/unregistered categories.
The checksum-bound layout contract handles split rows, spurious empty grid
columns, a dangling decimal split across a grid boundary, and a raised footnote
digit using glyph size/geometry rather than truncating string suffixes. Every
supported canonical table row is compared in its exact page/period/identity scope.
Repeated printed evidence is accepted only when all candidates agree; a conflicting
candidate is never hidden by a matching one. The parser is source-specific, not a
claim of generic support for every publication or future edition.

All packets retain `certification_status: pending` and
`publication_authorized: false`, even when every selected anchor matches. Their
`snapshot_sha256` is comparable with current QA; `source_facts_sha256`,
`source_cells_sha256` and `review_packet_sha256` additionally bind the review/parser
versions, exact parsed facts and output. A packet is
not operator evidence accepted by `sika.review approve`: it deliberately contains no
`verified_observation_ids` or manufactured source/revision/rights review reasons.

The repository is public. Full work packets stay under ignored `docs/reports/local/`;
`--summary-only` exports counts, IDs, statuses and hashes but no observation values,
raw originals, source-cell payloads, archive paths or attribution/licence text. The
public summary retains the full packet hash for correspondence, not authority. The
public quarantine table likewise omits original values; isolated source examples in
the narrative are not a downloadable draft dataset or a licence grant.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m sika.source_review --target data/processed/sika_v2.db --legacy data/processed/sika.db --report docs/reports/local/S0_4_3_source_review.json
.\.venv\Scripts\python.exe -m sika.source_review --target data/processed/sika_v2.db --legacy data/processed/sika.db --summary-only --report docs/reports/S0_4_3_source_review.json
.\.venv\Scripts\python.exe -m pytest tests/test_source_review.py tests/test_source_tables.py -q
```

CLI exit 0 means the packet was generated without hard discrepancies, **not** that
certification succeeded. `NEEDS_REVIEW` remains printed. Exit 1 means hard failures;
exit 2 means input/schema/I/O failure. Identical input bytes and DB facts produce an
identical packet; there is no execution timestamp masquerading as a diffusion date.

## Real corpus result

[Committed redacted audit summary](reports/S0_4_3_source_review.json). The complete
local review packet is `docs/reports/local/S0_4_3_source_review.json`:

| Release | Canonical rows | Exact source comparisons matched | Rows without matching evidence |
|---|---:|---:|---:|
| BCEAO June 2023 | 2 | 2 | 0 |
| INSEED BMS September 2025 | 7 | 7 | 0 |
| INSEED IHPC May 2026 | 118 | 118 | 0 |
| INSEED IHPC June 2026 | 115 | 115 | 0 |
| Total | 242 | 242 | 0 |

There are zero discrepancies on the full canonical cohort and zero current technical
hard failures. `source_comparison_status` is `complete`; `certification_status`
remains `pending`. Complete source concordance still does not authorize a release:
rights, release dates, row revision status and authenticated operator review remain
separate requirements. All 242 observations and four releases remain `draft`, with
zero review decisions or publication events. The global logical snapshot remains
`2062834c0df68553b48c4c7ce1e6ce6aa50cfbf61bc1af624b03aa036f7f2abe`, identical to
the S0.4.2 QA snapshot. Both local database files remain unchanged.

The 76 quarantines remain intentionally outside this canonical cohort; their triage
does not grant numeric/domain or publication approval. The read-only tool's
`source_values_review_pending` blocker means **formal authorized review is pending**,
not that 207 numeric checks remain undone. All numeric comparisons are now finished.

### Verification

The full suite passes **351 tests** without live provider calls, including 68
source-review checks and 27 deterministic table-parser checks. They cover every
identity field, exact Decimal equality, source/header shifts, footnote geometry,
malformed/missing source cells, changed bytes/profile checksums, orphan visibility,
read-only byte/state/history preservation, schema rejection, rights-evidence
diagnostic parity, repeated/conflicting source cells, redacted public summaries,
byte-stable exports, CLI exits and protected-file hardlink aliases. The existing
Starlette test-client deprecation warning remains.

On the real corpus, the preserved MVP database SHA-256 is
`47663a2967c4193a272aa24fc053ab8f272c2f4ba555006accb2a86d0f5073ab`; the V2 file is
`0bed1caedf5a9fc2d8cd9770aa2484319711137751ccdc6af2a18a22bb621fd4`.
All 16 pre-existing QA records remain, with zero review decisions/publication events.

### Source inspection findings

The validation/PDF workflow kept statistical evidence separate from publication
authority and required visual inspection of headings, cells and footnotes:

- Both IHPC covers, physical page 1, say `Informations préliminaires`. This is
  release-level provisional evidence, not proof that every historical cell has the
  same row-level revision status. The stored `unknown` marks are not silently edited.
- June's global index is **106.2**, YoY **0.1%**, monthly change **1.2%** and
  three-month change **1.7%** on physical page 1, Table 1. The current canonical rows
  already match; an old UI screenshot is not evidence that these V2 values are wrong.
- IHPC regional Table 2, physical page 5, covers **April 2026** in the May bulletin
  and **May 2026** in the June bulletin. Canonical periods already preserve that lag.
  Bulletin filenames must not replace the periods printed in individual tables.
- BMS physical page 8, Table 2, explicitly prints base **2014** and September 2025
  index **102.0**, monthly **-1.7%**, annual **-0.6%**. Its Table 1's **0.8%** lacks
  a defined window on that page; it must not be substituted as point-to-point YoY.
  Physical page 9, Table 3, preserves energy **103.5** and fresh products **101.2**.
- BCEAO physical page 13 (printed page 3), Summary paragraph 7, gives UEMOA YoY
  **5.8%** for 2023-Q1 and **7.8%** a quarter earlier. It does not certify a full
  history or a final revision flag merely by providing these two figures.
- The June IHPC weight heading in Table 5 is marked with note 3. Physical page 6
  states the groups' denominator is **100000**. Thus weight **16499** on physical
  page 7 is not itself a corrupt value. Its legacy `per_10000` unit is wrong;
  page-2 division weights use denominator **10000**, not their legacy `‰` unit.
  None of these weights is admitted as an inflation-rate/index observation.

## Individual quarantine triage

The [quarantine registry](S0_4_3_QUARANTINE_REVIEW.md) lists exactly the 76 preserved
legacy IDs with document/page/table facts and a proposed disposition:

| Recommendation | Rows |
|---|---:|
| Geography/methodology outside the admitted pilot | 28 |
| Not observed CPI inflation: 13 retail-price changes and two forecasts | 15 |
| Source/unit denominator mismatch, outside the admitted weight domain | 26 |
| Window/annual aggregation still unresolved | 7 |

These recommendations are not database decisions. No quarantine is accepted by
reason group, deleted or silently reclassified. A future correction must preserve
original audit and use a reviewed versioned mapping/new linked release as appropriate.

## Rights investigation: unknown, not prohibited

The owner confirms no written commercial permission currently exists. That does not
prove a permission is required for every individual statistic, nor that commercial
use is forbidden. Public availability is not by itself verified evidence of an
applicable licence for each exposure/redistribution mode. This is an operational
rights check, not a legal opinion.

Official evidence checked on 2026-10-11:

- [INSEED diffusion policy](https://inseed.tg/download/5811/?tmstv=1755330733),
  section 3.2.2, physical/printed page 26, distinguishes availability from conditions
  of reproduction and transfer. It does not grant a verified blanket commercial
  licence to these three macro publications. Annex 7 (physical pages 155-157) is a
  research/teaching data-request undertaking; its applicability to freely published
  aggregated macro bulletins is not established. Do not apply microdata restrictions
  automatically to macrostatistics.
- [INSEED Inflation/Prix](https://inseed.tg/inflation-prix/) identifies the May/June
  editions and download links. [Official reports](https://inseed.tg/rapports-danalyse/)
  lists BMS September 2025. These establish publication identity/availability, not
  exact diffusion dates or a verified commercial licence.
- [BCEAO's official June 2023 report page](https://www.bceao.int/fr/publications/rapport-sur-la-politique-monetaire-de-lumoa-juin-2023)
  is dated **07 July 2023**. No applicable commercial licence was verified on that
  page or in the archived PDF. The current download button points to a different
  filename/URL from the migrated archive; an exact-vintage association is still
  needed before recording its diffusion date as immutable provenance.

All four releases therefore keep source reuse `unknown`, not `allowed` or
`prohibited`. No publisher was contacted, no permission requested or obtained,
and no production data publication or hosting deployment was performed.

### Dates and vintage evidence

PDF creation/modification metadata is an investigative clue, not an authoritative
release date: May IHPC says 2026-06-10, June IHPC 2026-07-10, BMS September 2025 says
2026-06-24, and BCEAO says 2023-07-03. The BMS example especially disproves deriving
`published_on` from its reference month or a local file timestamp.

The exact BCEAO archived URL is
`https://www.bceao.int/sites/default/files/2023-07/Rapport%20sur%20la%20politique%20mone%CC%81taire%20-%20Juin%202023.pdf`.
The official page's current button instead points to
`https://www.bceao.int/sites/default/files/2023-07/BCEAO%20-%20Rapport%20sur%20la%20politique%20mone%CC%81taire_JUIN_2023.pdf`.
Do not replace archived bytes or checksums silently.

## Remaining acceptance work

1. Have an authorized reviewer accept the complete 242-row source comparison and
   resolve each row's revision evidence. The full numeric check is finished; a
   machine match or agent inspection is not the authenticated approval decision.
2. Establish applicable reuse terms for cited figures, reproduced text/tables,
   commercial charts, CSV/API/database redistribution and archive access separately.
   Ask the publishers for clarification if published terms do not establish scope;
   actual contact requires the owner's explicit authorization.
3. Establish exact diffusion dates/vintage association, then register justified
   metadata through the provenance-preserving workflow. Never fabricate a date,
   mark a row final by default, or edit an immutable release to force PASS.
4. Obtain a real externally allowlisted operator's complete source review and fresh
   QA, then use the existing review/publication service. Only justified cohorts
   may become public. S0.4 and S0.4.3 remain in progress until this DoD passes.

The [publisher clarification drafts](S0_4_3_PUBLISHER_CLARIFICATION.md) are ready
for owner validation. They have not been sent and establish no permission. No
additional numeric/source implementation work remains on the current pilot.
