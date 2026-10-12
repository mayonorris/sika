# S0.4.3: Inflation quarantine source review

Status: read-only triage recommendations, inspected on 2026-10-11. **No row is
approved, corrected, imported or published by this document.** All 76 original
quarantines remain intact in `inflation_migration_rows`, run 1. This is evidence for
the subsequent operator review, not a substitute for it.

## Result and boundary

Every quarantined legacy ID is listed once below. Original values are retained in
the ignored local review packet, not copied into these public per-ID tables. Periods
are the original legacy fields, not proposed replacements. The complete original labels, confidence,
indicator keys, geography, document and page remain authoritative in `original_row`;
this report never rewrites them. Pages are **physical, 1-based PDF pages**, not the
bulletin's printed page numbers.

| Proposed triage | Rows | Meaning |
|---|---:|---|
| `wrong_scope` | 28 | Foreign-country inflation outside the reviewed West African pilot; printed figures corroborated, methodology not admitted. |
| `verified_non_domain` | 15 | Two explicit forecasts and 13 regional retail-price changes, not canonical observed IHPC inflation. |
| `source_mismatch` | 26 | Printed weight values match, but the stored unit uses the wrong denominator. Weights are also outside the admitted price/index concepts. |
| `remain_unresolved` | 7 | Six unspecified inflation windows and one annual aggregation require a reviewed concept/methodology extension. |
| `value_conflict` | 0 | No quarantined value conflict established by the inspected source text. This does not certify other canonical rows. |
| Total | 76 | No release-wide or reason-wide acceptance. |

Sources inspected directly with local `pdfplumber`, without a model or API request:

| Exact filename | SHA-256 | Inspected physical pages relevant to quarantine |
|---|---|---|
| `bceao_politique_monetaire_2023-06.pdf` | `ae86c55b3364fd2ee9c29112e258a0386e4fbfefa4bfdeab9979e7be62cbc1fd` | 13, 15, 16 |
| `inseed_bulletin_mensuel_2025-09.pdf` | `21ab8c005358b5b2090e1804403749fa8c3230d0132dd9d12446062d5451de64` | 8, 13, 14, 15 |
| `inseed_ihpc_2026-06.pdf` | `792b218f6159ecd43d5fa9fbea7d79552fa5f458841d79d4e7be0e55ed8dfb30` | 1, 2, 4, 5, 6, 7 |

The source manifest comes from the preserved S0.3 migration run. The exact legacy
snapshot is SHA-256
`47663a2967c4193a272aa24fc053ab8f272c2f4ba555006accb2a86d0f5073ab`.
Text extraction supports the proposed triage; visual inspection of important cells
and source footnotes remains a distinct verification step before certification.
The public repository stores only this redacted triage and ID/hash/count summary.
The detailed work packet stays under ignored `docs/reports/local/`.

## BCEAO: 31 original rows

Exact document: `bceao_politique_monetaire_2023-06.pdf`. All rows below retain unit `%`.
The foreign-country entries are not false merely because they are quarantined. They
are outside the current source profile; admitting them needs reviewed geographies,
concepts and methodologies. Equal numbers on pages 13 and 16 must not be merged by
label alone. In particular, the US paragraph explicitly names PCE, while the preceding
summary uses the generic word inflation; those concepts must remain distinguishable.

| Legacy ID | Page | Original concept | Original geography | Original period | Proposed triage | Source locator / reason |
|---|---:|---|---|---|---|---|
| 436 | 13 | `inflation_rate_yoy` | Zone euro | 2023-03 | `wrong_scope` | Summary paragraph 4, March euro-area inflation. |
| 437 | 13 | `inflation_rate_yoy` | Zone euro | 2022-12 | `wrong_scope` | Summary paragraph 4, December euro-area comparison. |
| 438 | 13 | `inflation_rate_yoy` | États-Unis | 2023-03 | `wrong_scope` | Summary paragraph 4, March US inflation; do not infer PCE solely from a matching number. |
| 439 | 13 | `inflation_rate_yoy` | États-Unis | 2022-12 | `wrong_scope` | Summary paragraph 4, December US comparison. |
| 440 | 13 | `inflation_rate_yoy` | Royaume-Uni | 2023-03 | `wrong_scope` | Summary paragraph 4, March UK price increase. |
| 441 | 13 | `inflation_rate_yoy` | Royaume-Uni | 2022-12 | `wrong_scope` | Summary paragraph 4, December UK comparison. |
| 442 | 13 | `inflation_rate_yoy` | Chine | 2023-03 | `wrong_scope` | Summary paragraph 4, March emerging-economy figures, first country. |
| 443 | 13 | `inflation_rate_yoy` | Chine | 2022-12 | `wrong_scope` | Summary paragraph 4, December comparison, first country. |
| 444 | 13 | `inflation_rate_yoy` | Inde | 2023-03 | `wrong_scope` | Summary paragraph 4, March figures, second country. |
| 445 | 13 | `inflation_rate_yoy` | Inde | 2022-12 | `wrong_scope` | Summary paragraph 4, December comparison, second country. |
| 446 | 13 | `inflation_rate_yoy` | Afrique du Sud | 2023-03 | `wrong_scope` | Summary paragraph 4, March figures, third country. |
| 447 | 13 | `inflation_rate_yoy` | Afrique du Sud | 2022-12 | `wrong_scope` | Summary paragraph 4, December comparison, third country. |
| 513 | 15 | `inflation_rate_yoy` | UEMOA | 2022 | `remain_unresolved` | Paragraph 17 explicitly gives the 2022 historical annual rate. An annual-average concept and its aggregation need review; do not call it monthly YoY. |
| 514 | 15 | `inflation_rate_yoy` | UEMOA | 2023 | `verified_non_domain` | Paragraph 17: forward-looking wording `devrait ... s'établir`; forecast, not observed 2023 data. |
| 515 | 15 | `inflation_rate_yoy` | UEMOA | 2024 | `verified_non_domain` | Paragraph 17: same projected path for 2024; forecast, not observed data. |
| 528 | 16 | `inflation_rate_yoy` | Zone euro | 2023-03 | `wrong_scope` | Global-developments introduction, March euro-area figure. |
| 529 | 16 | `inflation_rate_yoy` | Zone euro | 2022-12 | `wrong_scope` | Global-developments introduction, December euro-area comparison. |
| 530 | 16 | `inflation_rate_yoy` | États-Unis | 2023-03 | `wrong_scope` | Global-developments introduction, generic March US figure. |
| 531 | 16 | `inflation_rate_yoy` | États-Unis | 2022-12 | `wrong_scope` | Global-developments introduction, generic December US comparison. |
| 532 | 16 | `inflation_rate_yoy` | Royaume-Uni | 2023-03 | `wrong_scope` | Global-developments introduction, March UK figure. |
| 533 | 16 | `inflation_rate_yoy` | Royaume-Uni | 2022-12 | `wrong_scope` | Global-developments introduction, December UK comparison. |
| 534 | 16 | `inflation_rate_yoy` | Chine | 2023-03 | `wrong_scope` | Global-developments introduction, March emerging-economy figures, first country. |
| 535 | 16 | `inflation_rate_yoy` | Chine | 2022-12 | `wrong_scope` | Global-developments introduction, December comparison, first country. |
| 536 | 16 | `inflation_rate_yoy` | Inde | 2023-03 | `wrong_scope` | Global-developments introduction, March figures, second country. |
| 537 | 16 | `inflation_rate_yoy` | Inde | 2022-12 | `wrong_scope` | Global-developments introduction, December comparison, second country. |
| 538 | 16 | `inflation_rate_yoy` | Afrique du Sud | 2023-03 | `wrong_scope` | Global-developments introduction, March figures, third country. |
| 539 | 16 | `inflation_rate_yoy` | Afrique du Sud | 2022-12 | `wrong_scope` | Global-developments introduction, December comparison, third country. |
| 542 | 16 | `pce_inflation_rate` | États-Unis | 2023-03 | `wrong_scope` | Section 1.1, paragraph 1, right column: explicit personal-consumption-expenditure index. |
| 543 | 16 | `pce_inflation_rate` | États-Unis | 2022-12 | `wrong_scope` | Same paragraph: `trois mois plus tôt`, compared with March 2023. |
| 544 | 16 | `core_pce_inflation_rate` | États-Unis | 2023-03 | `wrong_scope` | Same paragraph: underlying inflation excluding food and energy. |
| 545 | 16 | `core_pce_inflation_rate` | États-Unis | 2022-12 | `wrong_scope` | Same paragraph, explicitly December 2022. |

## September INSEED bulletin: 19 original rows

Exact document: `inseed_bulletin_mensuel_2025-09.pdf`. All rows below retain unit `%`.
Physical page 8 is printed page 1. Physical pages 13, 14 and 15 are printed pages 6,
7 and 8 respectively.

### Six inflation-window ambiguities

Table 1 on physical page 8 gives the original values retained in the local packet,
but does not define their comparison windows. Table 2 on the same page instead gives the global index as 102.6 in September
2024 and 102.0 in September 2025, with its printed annual change **-0.6%**, not Table
1's 0.8%. This is direct evidence against treating every `Taux d'inflation` label as
point-to-point YoY. The June 2026 IHPC publication, physical page 4, defines its headline
inflation using the mean of the last 12 months against the preceding 12-month mean;
that supports a plausible interpretation, but does not by itself certify the earlier
September bulletin's window. The underlying-inflation window also needs evidence.

| Legacy ID | Page | Original concept | Original label | Geography | Period | Proposed triage | Source locator / unresolved field |
|---|---:|---|---|---|---|---|---|
| 908 | 8 | `inflation_rate_yoy` | Taux d'inflation | Togo | 2024-09 | `remain_unresolved` | Table 1, headline row, Sept-24; comparison window unspecified. |
| 909 | 8 | `inflation_rate_yoy` | Taux d'inflation | Togo | 2025-08 | `remain_unresolved` | Table 1, headline row, Août-25; comparison window unspecified. |
| 910 | 8 | `inflation_rate_yoy` | Taux d'inflation | Togo | 2025-09 | `remain_unresolved` | Table 1, headline row, Sept-25; differs conceptually from Table 2's -0.6 annual index change. |
| 911 | 8 | `core_inflation_rate` | Inflation sous-jacente | Togo | 2024-09 | `remain_unresolved` | Table 1, underlying row, Sept-24; basket and comparison window unspecified here. |
| 912 | 8 | `core_inflation_rate` | Inflation sous-jacente | Togo | 2025-08 | `remain_unresolved` | Table 1, underlying row, Août-25; basket and comparison window unspecified here. |
| 913 | 8 | `core_inflation_rate` | Inflation sous-jacente | Togo | 2025-09 | `remain_unresolved` | Table 1, underlying row, Sept-25; basket and comparison window unspecified here. |

### Thirteen regional retail-price changes

Each row below is literally corroborated in the indicated regional price table,
column `Variation (%) / Mensuelle`. Each keeps original concept
`inflation_rate_mom`, period `2025-09`, unit `%`; that concept is not justified because
these are individual product-price changes, not an IHPC basket or national total.
Retain the originals in quarantine. A future retail-price domain must supply product,
region, package/unit and price-change identity rather than broad-accepting them as CPI.

| Legacy ID | Page | Original geography | Product in original label | Proposed triage | Source locator |
|---|---:|---|---|---|---|
| 923 | 13 | Togo - Région des Plateaux-Ouest | Riz importé longs grains vendu au bol (Kg) | `verified_non_domain` | Table 8, rice row, monthly-change column. |
| 926 | 13 | Togo - Région des Plateaux-Ouest | Maïs blanc séché en grains crus vendu au grand bol (Kg) | `verified_non_domain` | Table 8, maize row, monthly-change column. |
| 929 | 13 | Togo - Région des Plateaux-Ouest | Viande de bœuf avec os au kg (Kg) | `verified_non_domain` | Table 8, beef row, monthly-change column. |
| 932 | 13 | Togo - Région des Plateaux-Ouest | Oignon frais rond (Kg) | `verified_non_domain` | Table 8, onion row, monthly-change column. |
| 935 | 14 | Togo - Région Centrale | Riz importé longs grains vendu au bol (Kg) | `verified_non_domain` | Table 9, rice row, monthly-change column. |
| 938 | 14 | Togo - Région Centrale | Maïs blanc séché en grains crus vendu au grand bol (Kg) | `verified_non_domain` | Table 9, maize row, monthly-change column. |
| 941 | 14 | Togo - Région Centrale | Igname (Kg) | `verified_non_domain` | Table 9, yam row, monthly-change column. |
| 944 | 14 | Togo - Région de la Kara | Riz importé longs grains vendu au bol (Kg) | `verified_non_domain` | Table 10, rice row, monthly-change column. |
| 947 | 14 | Togo - Région de la Kara | Maïs blanc séché en grains crus vendu au grand bol (Kg) | `verified_non_domain` | Table 10, maize row, monthly-change column. |
| 950 | 14 | Togo - Région de la Kara | Igname (Kg) | `verified_non_domain` | Table 10, yam row, monthly-change column. |
| 953 | 15 | Togo - Région des Savanes | Riz importé longs grains vendu au bol (Kg) | `verified_non_domain` | Table 11, rice row, monthly-change column. |
| 956 | 15 | Togo - Région des Savanes | Maïs blanc séché en grains crus vendu au grand bol (Kg) | `verified_non_domain` | Table 11, maize row, monthly-change column. |
| 959 | 15 | Togo - Région des Savanes | Igname (Kg) | `verified_non_domain` | Table 11, yam row, monthly-change column. |

## June INSEED IHPC: 26 original weight rows

Exact document: `inseed_ihpc_2026-06.pdf`. All rows retain original concept
`cpi_subindex_weight` and geography `Togo`. Printed weight numbers match the stored
numbers, but **the source uses two different denominators in one publication**:

- Table 1, physical pages 1-2, has a global weight of 10000. The twelve page-2 rows
  were stored as `‰`; that denotes a denominator of 1000, not 10000.
- Table 5, physical pages 6-7, marks its weight heading with footnote 3. The footnote
  on physical page 6 explicitly says weights are normalized to **100000** to avoid
  zero weights for small groups. The fourteen page-7 rows were stored as
  `per_10000`; that is not the source scale.

Consequently the food-service weight **16499** on page 7 is not, by itself, an
out-of-range source error: its documented denominator is 100000. Do not divide or
alter any original number to make it fit the old unit. A future weight-domain
extension must preserve original value/unit, record the verified source denominator,
and introduce a reviewed unit/identity through a separate correction mechanism.

| Legacy ID | Page | Original period | Weight label / row | Original unit | Printed denominator | Proposed triage |
|---|---:|---|---|---|---:|---|
| 265 | 2 | 2026-06 | II Boissons alcoolisées, tabacs et stupéfiants | `‰` | 10000 | `source_mismatch` |
| 270 | 2 | 2026-06 | III Articles d'habillement et chaussures | `‰` | 10000 | `source_mismatch` |
| 275 | 2 | 2026-06 | IV Logement, eau, gaz, électricité et autres combustibles | `‰` | 10000 | `source_mismatch` |
| 280 | 2 | 2026-06 | V Meubles, articles de ménage et entretien courant du foyer | `‰` | 10000 | `source_mismatch` |
| 285 | 2 | 2026-06 | VI Santé | `‰` | 10000 | `source_mismatch` |
| 290 | 2 | 2026-06 | VII Transports | `‰` | 10000 | `source_mismatch` |
| 295 | 2 | 2026-06 | VIII Information et Communication | `‰` | 10000 | `source_mismatch` |
| 300 | 2 | 2026-06 | IX Loisirs et culture | `‰` | 10000 | `source_mismatch` |
| 305 | 2 | 2026-06 | X Enseignement | `‰` | 10000 | `source_mismatch` |
| 310 | 2 | 2026-06 | XI Restaurants et services d'hébergement | `‰` | 10000 | `source_mismatch` |
| 315 | 2 | 2026-06 | XII Assurance et services financiers | `‰` | 10000 | `source_mismatch` |
| 320 | 2 | 2026-06 | XIII Soins personnels, protection sociale et biens divers | `‰` | 10000 | `source_mismatch` |
| 376 | 7 | 2026 | Jardins et animaux | `per_10000` | 100000 | `source_mismatch` |
| 379 | 7 | 2026 | Services de loisirs | `per_10000` | 100000 | `source_mismatch` |
| 382 | 7 | 2026 | Biens culturels | `per_10000` | 100000 | `source_mismatch` |
| 385 | 7 | 2026 | Services culturels | `per_10000` | 100000 | `source_mismatch` |
| 388 | 7 | 2026 | Journaux, livres et papeterie | `per_10000` | 100000 | `source_mismatch` |
| 391 | 7 | 2026 | Voyages à forfait | `per_10000` | 100000 | `source_mismatch` |
| 394 | 7 | 2026 | Petite enfance et enseignement primaire | `per_10000` | 100000 | `source_mismatch` |
| 397 | 7 | 2026 | Enseignement secondaire | `per_10000` | 100000 | `source_mismatch` |
| 400 | 7 | 2026 | Enseignement postsecondaire non supérieur | `per_10000` | 100000 | `source_mismatch` |
| 403 | 7 | 2026 | Enseignement tertiaire | `per_10000` | 100000 | `source_mismatch` |
| 406 | 7 | 2026 | Services de service de nourriture et de boissons | `per_10000` | 100000 | `source_mismatch` |
| 409 | 7 | 2026 | Services d'hébergement | `per_10000` | 100000 | `source_mismatch` |
| 412 | 7 | 2026 | Assurance | `per_10000` | 100000 | `source_mismatch` |
| 415 | 7 | 2026 | Biens et services pour soins personnels | `per_10000` | 100000 | `source_mismatch` |

The original yearly period `2026` in page-7 rows is also not proof of an annual
observed weight series. The publication attributes weights to EHCVM 2021 and uses a
2023 index base on page 1. Temporal applicability and weight metadata require their
own domain contract; no monthly/annual price-series mapping is recommended here.

## Source facts and reuse rights are separate questions

Corroborating a printed value does not establish a right to redistribute a document,
passage or database product commercially. Conversely, an unresolved permission does
not establish prohibition. The project owner reports no written commercial permission.
Keep reuse `unknown` unless applicable publisher terms or documented permission are
reviewed and support the specific intended use. This report does not supply a legal
opinion or declare these official facts commercially unusable.

Publication dates must not be filled from an observation month or a local download
timestamp. The June cover's `Informations préliminaires` is relevant evidence of a
provisional release, not a verified calendar publication date. Preserve provenance
and the publication boundary documented in [the quality gate](S0_4_QUALITY_GATE.md).

## Smallest safe certification-tooling contract

This is a suggested interface, not a claim that the following tooling is implemented:

1. Export a read-only review packet bound to the current schema, complete snapshot
   hash, source checksums, legacy snapshot hash and exact migration run. Include every
   canonical observation and every quarantine original with ID and original-row hash.
2. Record a bounded per-row recommendation with the five triage codes above, a source
   document/page/table/row/column locator and a nonempty reason. Compare value, period,
   unit and concept separately; no model-generated value or default-verified field.
3. Require exhaustive unique ID coverage for quarantine triage. Reject an altered
   original hash, omitted/extra ID, changed source, invalid page or conflicting field.
   Preserve original audit JSON; corrections create linked evidence and a reviewed
   versioned mapping or new release, never an in-place rewrite to force PASS.
4. Keep rights, release date, revision evidence and source-value checks distinct. An
   unresolved field blocks certification of the relevant cohort. A triage report is
   never a lifecycle transition or a licence grant.
5. Only the existing authenticated local operator service can approve/publish after
   fresh technical validation and complete source review. Exporting or completing a
   proposed packet never configures an operator or publishes data automatically.

The intentionally unresolved six windows and annual aggregation remain visible; the
future weight and retail-price domains are not introduced opportunistically by S0.4.3.
