# S0.3: Inflation snapshot migration and reconciliation

## Outcome and boundary

The local migration accounts for every inflation-family row in the committed legacy
snapshot. It does not change `data/processed/sika.db`, the API, the UI or publication
state. All canonical observations and releases remain `draft`; review and publication
belong to S0.4. No API call, new dependency or numeric re-extraction is involved.

The quality review separated measurement windows, categories and base years before
mapping. It deliberately retains ambiguous records in quarantine instead of resolving
them from extraction confidence.

Machine-readable evidence: [complete reconciliation report](reports/S0_3_inflation_reconciliation.json).
The report includes every legacy ID, disposition, reason, value, label, document and
physical page, plus ten example rows and verified source checksums.

Legacy snapshot SHA-256:
`47663a2967c4193a272aa24fc053ab8f272c2f4ba555006accb2a86d0f5073ab`.
Mapping version: `inflation-2026-09-v1`. Schema revision: `0002_inflation_audit`.

| Document | Input rows | Canonical rows | Merged duplicates | Quarantined |
|---|---:|---:|---:|---:|
| INSEED IHPC, May 2026 | 122 | 118 | 4 | 0 |
| INSEED IHPC, June 2026 | 152 | 115 | 11 | 26 |
| INSEED monthly bulletin, September 2025 | 26 | 7 | 0 | 19 |
| BCEAO monetary policy report, June 2023 | 33 | 2 | 0 | 31 |
| Total | 333 | 242 | 15 | 76 |

There are 149 distinct canonical series, 257 legacy-to-observation links, four
releases, and 55 preserved passages. The scope includes all legacy keys containing
`inflation`, all `cpi_` keys and the explicitly listed consumer-price index keys,
even when they are subsequently quarantined. All 333 selected legacy rows had
confidence >= 0.5; that threshold is only the old eligibility rule, not certification.

## Reviewed identity rules

Source profiles in `sika/inflation_mapping.py` bind interpretations to the exact
local documents. File hashes and source URLs are recorded without a new download.
Page numbers below are physical PDF pages, not printed page labels.

- `inseed_ihpc_2026-05.pdf` and `inseed_ihpc_2026-06.pdf`, pages 1-2, 4-5 and 7:
  distinguish all-items, consumption divisions, detailed groups and secondary groups;
  index base 2023; separate monthly, year-on-year, rolling three-month and rolling
  twelve-month-average changes. The old monthly `inflation_rate_qoq` is a three-month
  change, not a calendar-quarter observation. A country's regional-comparison figure
  uses that country's geography, not Togo by default. Source variant:
  `waemu:ihpc_2023`.
- `inseed_bulletin_mensuel_2025-09.pdf`, physical pages 8-9: preserve the explicitly
  printed index base 2014 in its own series, even though the classification may look
  similar to later tables. The page-8 heading was also visually checked. Do not
  silently rebase it. Source variant: `inseed:bms_2014_as_printed`. The bare inflation
  and core-inflation labels do not identify their averaging window, so they stay
  quarantined.
- `bceao_politique_monetaire_2023-06.pdf`, pages 13 and 15: retain only the reviewed
  quarterly WAEMU year-on-year series from page 13 in this pilot. Annual 2023/2024
  figures on page 15 are forecasts, not
  observed inflation. Other-country methodologies and the annual 2022 aggregation
  remain outside the reviewed mapping. Source variant:
  `bceao:regional_quarterly_inflation`.

Categories use exact normalized aliases, never fuzzy substring matching. Food is not
food-and-non-alcoholic-beverages; a detailed group is not the headline. Units map to
`percent` or `index` without value conversion. Seasonal adjustment is `not_reported`,
not assumed to be unadjusted. Source revision status remains `unknown` because legacy
rows did not record individual revision marks; source certification must resolve
that in S0.4 before approval. Series labels are display metadata; complete identity
and category must be used for future catalog rendering.

## Quarantine and duplicates

| Reason | Rows | Required follow-up |
|---|---:|---|
| Other-country methodology outside reviewed pilot | 28 | Review each source/methodology before extending coverage. |
| CPI weight scale not reviewed | 26 | Reconcile the printed scale; never infer a rescaling from a large value. |
| Individual retail-price changes, not CPI | 13 | Route to a separate retail-price domain. |
| Inflation averaging window ambiguous | 6 | Establish the exact definition from official metadata. |
| Forecast, not observation | 2 | Keep excluded from the observed-statistics product. |
| Annual aggregation not reviewed | 1 | Establish the annual statistic's definition. |

The 15 merged duplicate pairs have identical values and complete identities within
one release. A deterministic primary citation uses the lowest physical page, then ID.
Every alternative label/page remains in `legacy_observation_links.original_row` and
the exhaustive migration audit. A duplicate across two publications remains two
vintages, not a silent deletion. Conflicting values are quarantined as a group.

## Validation and interpretation

- Persisted values compare exactly with the legacy numeric values using decimal
  comparison; no unexplained missing ID, merge, citation change or identity change.
- Period syntax/frequency, allowed canonical units, full-key uniqueness and provenance
  pass migration checks. Outliers are evaluated within complete series, not across
  unrelated categories. One warning remains: the reviewed BCEAO subset has only two
  canonical observations. Short histories cannot establish statistical reliability.
- Exact replay produces the same reconciliation JSON and no additional rows. Source
  archive hashes are rechecked. The legacy database hash is unchanged.
- Tests exercise populated downgrade/rebuild, transaction interruption/retry,
  missing/replaced source files, unsafe filenames, changed snapshots, conflicting
  observations/passages, ambiguous mappings and corrupted persisted data.
- The unchanged legacy validator still exits 1: 136 hard findings and 8 warnings on
  its full 1,237-row corpus. Its old identity key conflates distinct categories and
  its unit vocabulary finds existing raw variants. This is a recorded baseline, not
  a claim that legacy quality failures are fixed. The canonical migration is checked
  against the V2 identity; it is not validated by pointing the old script at V2 tables.

Reconciliation proves migration fidelity, not that every historical extraction was
correct against the original PDF. The source-specific mapping review is not a human
publication approval. In particular, source rights, preliminary/revision marks and
the unresolved quarantine still need the S0.4 review workflow. No public read switches
to this database until the quality gate and safe query compiler are ready.

## Reproduce locally

Run at the repository root, with the original four PDF files in `data/raw/` and the
exact legacy snapshot. Use a new target for any changed input or mapping version.

```powershell
$env:SIKA_DATABASE_URL = 'sqlite:///data/processed/sika_v2.db'
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m sika.migrate_inflation --target data/processed/sika_v2.db --report docs/reports/S0_3_inflation_reconciliation.json
$env:OPENAI_API_KEY = 'test'
.\.venv\Scripts\python.exe -m pytest -q
```

The importer requires an already versioned, empty target on its first run. It refuses
the legacy DB itself, a populated unrelated target, an active legacy SQLite journal,
or changed inputs on replay. It does not load `.env`. Repeat the same command to
verify idempotence. A missing or changed PDF quarantines its rows instead of creating
unverified canonical records; `PASS` means all dispositions reconcile, not that every
row was mapped. Inspect quarantine counts before any downstream decision.

`data/processed/sika_v2.db` and `data/processed/trust_core_sources/` are ignored by Git.
Keep them together for backup; release storage locators reference the exact local
archive paths. Moving/deploying releases requires a documented storage migration,
not overwriting immutable provenance. Only code and reconciliation evidence are
committed. Source reuse rights remain `unknown` and original publication dates remain
NULL where unverified. Retrieval timestamps describe this migration's archive capture.

## Rollback and recovery

No legacy data needs restoring because it is never written. On a disposable target,
the tested recovery is `alembic downgrade base`, `alembic upgrade head`, then the
same import. Never run downgrade on a valuable populated database without a verified
backup. Downgrading only 0002 discards audit tables, not canonical data, and is not a
complete import undo. The importer then refuses that nonempty target.

Archive copies can remain after a failed SQL transaction and are verified when reused.
A failed partial copy is rejected by its checksum; inspect and replace only that
unreferenced artifact before retrying, never silently overwrite a referenced source.
The final JSON report is written after DB commit: if report writing fails, replay the
same import to regenerate it. Live PostgreSQL migration/rollback remains a deployment
prerequisite; this importer intentionally handles local SQLite snapshots only.
