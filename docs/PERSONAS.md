# Personas — who Sika serves

Four primary personas, one secondary. Each defines a voice the UI must satisfy. The common trust requirement across all five: **a number without a source is worthless to them.**

## 1. Afi — applied researcher

Economics lecturer-researcher, Université de Lomé. Writing a paper on inflation pass-through in WAEMU.

- **Pain**: spends entire afternoons locating IHPC tables across INSEED monthly notes, retyping values into Excel, second-guessing her own typos.
- **Asks Sika**: "Donne-moi l'inflation mensuelle au Togo depuis 2023 avec les sources."
- **Aha moment**: the answer lists the series and the exact bulletin and page for each point; she cites Sika's sources, not Sika, in her paper. That distinction makes it usable in academia.
- **Would abandon if**: a single value proves wrong against the PDF. Accuracy > coverage.

## 2. Kossi — economic journalist

Reporter at a regional business outlet covering UEMOA economies on deadline.

- **Pain**: needs one verified number in the next 20 minutes; the BCEAO bulletin is a 90-page PDF; he quotes a competitor's article instead and hopes.
- **Asks Sika**: "What is the latest industrial production figure for Togo and how does it compare to last year?"
- **Aha moment**: a two-sentence sourced answer he can quote verbatim, with "(Bulletin INSEED T1 2026, p. 4)" ready for print.
- **Would abandon if**: answers are slow (> 30 s) or hedge vaguely instead of giving the number.

## 3. Awa — fintech / credit analyst

Risk analyst at a microfinance-focused fintech in Dakar evaluating the Togolese market.

- **Pain**: sector data on SFD (deposits, credit, portfolio quality) exists in BCEAO reports nobody at her company has time to mine; decisions default to gut feel.
- **Asks Sika**: "Évolution des dépôts et crédits de la microfinance au Togo, et génère-moi un brief risque."
- **Aha moment**: the Brief button produces a one-page sector note with cited figures she pastes into her investment memo. An hour of work in thirty seconds.
- **Would abandon if**: briefs pad missing data with generic prose instead of flagging the gap.

## 4. Sena — statistics student

Final-year ISE-track student preparing entrance exams and a thesis on public debt.

- **Pain**: doesn't know what official data even exists; discovers publications by luck; has no budget for data services.
- **Asks Sika**: browses the indicator catalog first, then "Quel est le seuil optimal de dette publique selon les études sur l'Afrique subsaharienne ?" (tests the honest-refusal path: that is literature, not official statistics).
- **Aha moment**: the catalog shows him in one screen what exists, for which years; Sika's refusal to answer the literature question with fake authority teaches him the tool's boundaries and earns trust.
- **Would abandon if**: the free tool gates content or the UI intimidates.

## 5. (Secondary) Directeur de cabinet — policy advisor

Prepares ministerial briefings. Uses only the Brief feature via an assistant. Needs: French, sober tone, printable, zero hallucination risk. He is why every brief ends with a sources list.

## Design implications (binding)

1. Citations are UI elements, not footnotes: rendered with every figure, verbatim quotable.
2. Bilingual mirror: answer language follows question language automatically.
3. Honest empty states: "Je n'ai pas cette donnée dans les publications ingérées (liste)." is a feature, not a failure.
4. Speed target: first token or spinner-with-status under 3 s; full answer under 15 s.
5. Zero onboarding: example question chips on the home screen replace any tutorial.
