# Data specification — Sika

The database is the product. This spec defines what a valid observation is, how raw PDF content becomes one, and how we verify it. Any change to these rules must be reflected in `pipeline/extract.py` and noted here.

## Tables

### observations
| column | type | rule |
|--------|------|------|
| indicator | TEXT | canonical snake_case English key (see registry below) |
| indicator_label | TEXT | original French label as printed in the source |
| geography | TEXT | controlled vocabulary: `Togo`, `UEMOA`, `Benin`, `Burkina Faso`, `Cote d'Ivoire`, `Guinee-Bissau`, `Mali`, `Niger`, `Senegal` |
| period | TEXT | normalized: `YYYY`, `YYYY-QN`, or `YYYY-MM` (zero-padded) |
| value | REAL | numeric only, normalized (see below) |
| unit | TEXT | controlled: `%`, `points`, `milliards FCFA`, `millions FCFA`, `index`, `unites` |
| source_doc | TEXT | exact filename in data/raw/ |
| source_page | INTEGER | 1-based PDF page |
| confidence | REAL | extractor self-assessment, 0-1 |

### passages
Raw page text (truncated 4000 chars) per (source_doc, page). Used for contextual retrieval and for spot-check verification.

### Uniqueness and deduplication
Logical key: (indicator, geography, period, source_doc). On conflict keep the higher-confidence row. Cross-document duplicates (same figure in two bulletins) are allowed and are a feature: they corroborate.

## Canonical indicator registry (seed; extend as corpus grows)

```
inflation_rate_yoy            Inflation, glissement annuel (%)
inflation_rate_mom            Inflation, variation mensuelle (%)
cpi_index                     IHPC, indice
industrial_production_index   IPI, indice
industrial_producer_price     IPPI, indice
turnover_index_industry       Indice du chiffre d'affaires industrie
gdp_growth                    Croissance du PIB reel (%)
gdp_nominal                   PIB nominal (milliards FCFA)
credit_to_economy             Credit a l'economie (milliards FCFA)
money_supply_m2               Masse monetaire M2 (milliards FCFA)
policy_rate                   Taux directeur BCEAO (%)
deposits_microfinance         Depots des SFD (milliards FCFA)
credit_microfinance           Credits des SFD (milliards FCFA)
microfinance_beneficiaries    Beneficiaires des SFD (unites)
portfolio_at_risk_microfinance Taux de degradation du portefeuille SFD (%)
exports_goods                 Exportations de biens (milliards FCFA)
imports_goods                 Importations de biens (milliards FCFA)
trade_balance                 Solde commercial (milliards FCFA)
public_debt_ratio             Dette publique / PIB (%)
fiscal_balance                Solde budgetaire (% PIB ou milliards FCFA)
```

Rule: if a table's concept has no canonical key, the extractor derives one (snake_case English) rather than dropping the row; new keys are reviewed at spot-check time.

## Normalization rules

- **Values**: French formats convert strictly: `1 234,5` -> 1234.5; `-` or `nd` or empty -> row skipped; parenthesized negatives `(12,3)` -> -12.3. Percent signs stripped, captured in unit.
- **Periods**: `T1 2026`/`1er trimestre 2026` -> `2026-Q1`; `janv-26`/`janvier 2026` -> `2026-01`; bare year -> `YYYY`. Cumulative labels (`Jan-Mar`) -> quarter if aligned, else skip.
- **Growth vs level**: a table column headed "variation (%)" produces a `*_rate` or `_yoy` indicator, never a level. Ambiguity -> lower confidence, not a guess.
- **Footnotes/revisions**: `(p)` provisional and `(r)` revised markers ignored for value, noted nowhere (MVP simplification).

## Confidence policy

- `>= 0.75` served in answers without caveat.
- `0.5 - 0.75` served, flagged internally; brief generation prefers higher-confidence rows.
- `< 0.5` inserted but excluded from `/ask` and `/brief` queries (`WHERE confidence >= 0.5`).

## Quality assurance protocol

1. **Automated, after every ingest run** (`pipeline/validate.py`, to build: BACKLOG T4): rows per document (alert if < 5), value outliers per indicator (|z| > 4 -> report), period format regex check, unit vocabulary check, duplicate report.
2. **Manual spot-check, before demo**: sample 10 random observations; open source_doc at source_page; verify value, period, unit. Target: 10/10. One failure -> fix extractor rule, re-run corpus, re-sample.
3. **Golden questions** (BACKLOG) re-run after any pipeline change.

## Source registry (fill URL when downloaded)

Registry populated 2026-07-18; download via `python pipeline/download_sources.py`.

| # | Publisher | Document | File | URL |
|---|-----------|----------|------|-----|
| 1 | INSEED | Bulletin mensuel des statistiques, sept. 2025 | inseed_bulletin_mensuel_2025-09.pdf | https://inseed.tg/download/7822/ |
| 2 | INSEED | Bulletin mensuel des statistiques, août 2025 | inseed_bulletin_mensuel_2025-08.pdf | https://inseed.tg/download/7819/ |
| 3 | INSEED | Bulletin mensuel des statistiques, juil. 2025 | inseed_bulletin_mensuel_2025-07.pdf | https://inseed.tg/download/7816/ |
| 4 | INSEED | Communiqué inflation, juin 2026 | inseed_communique_inflation_2026-06.pdf | https://inseed.tg/download/7876/ |
| 5 | INSEED | IHPC, juin 2026 | inseed_ihpc_2026-06.pdf | https://inseed.tg/download/7866/ |
| 6 | INSEED | IHPC, mai 2026 | inseed_ihpc_2026-05.pdf | https://inseed.tg/download/7798/ |
| 7 | INSEED | IHPC, avril 2026 | inseed_ihpc_2026-04.pdf | https://inseed.tg/download/7729/ |
| 8 | INSEED | IPI mensuel rénové 2015-2026 (Excel) | inseed_ipi_mensuel_2015-2026.xlsx | https://inseed.tg/download/7924/ |
| 9 | INSEED | IPI trimestriel, T4 2025 (Excel) | inseed_ipi_trimestriel_2025-T4.xlsx | https://inseed.tg/download/7551/ |
| 10 | INSEED | Premières estimations PIB 2025 | inseed_pib_estimations_2025.pdf | https://inseed.tg/download/7746/ |
| 11 | INSEED | Comptes nationaux trimestriels, T4 2025 | inseed_comptes_trimestriels_2025-T4.pdf | https://inseed.tg/download/7750/ |
| 12 | INSEED | Indice CA services, T1 2026 (Excel) | inseed_ica_services_2026-T1.xlsx | https://inseed.tg/download/7894/ |

Excel ingestion rule: .xlsx sources are parsed deterministically with pandas/openpyxl (no LLM for table values); the LLM may only assist with header/indicator labeling. Same observation schema and provenance rules apply (source_page = sheet name index, noted as page 1 if single-sheet).
| 13 | BCEAO | Rapport politique monétaire, juin 2023 | bceao_politique_monetaire_2023-06.pdf | bceao.int (sites/default/files/2023-07) |

To add later (recent editions are behind a JS listing; grab manually when energy returns): BCEAO bulletin mensuel de statistiques récent, statistiques SFD/microfinance, balance des paiements Togo.

Licensing note: these are public official publications; Sika stores extracted facts (not copyrightable) plus short passages for verification, and links every fact back to its source. Attribution is systematic by design.
