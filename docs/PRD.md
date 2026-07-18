# PRD — Sika

## One-liner

Ask West Africa's economy anything: official statistics, locked in PDFs, become a citable database you can query in plain French or English, with sourced answers, instant charts, and one-click economic briefs.

## The problem

Official economic data for West Africa exists, but it is functionally invisible. INSEED (Togo), BCEAO, and WAEMU publish rigorous statistics as PDF bulletins: inflation, industrial production, national accounts, credit, microfinance. To answer a question as simple as "how has inflation in Togo evolved since 2023?", a researcher opens five PDFs, hunts through tables, and retypes numbers by hand. Consequences, observed first-hand from inside Togo's national statistics office:

- Researchers and students waste hours on retrieval instead of analysis.
- Journalists publish without checking official figures, or don't publish at all.
- Fintechs and analysts price West African risk with stale or third-hand data.
- The statistical offices' own work is under-used, weakening the case for funding it.

No Bloomberg terminal covers this. No API exists. The data is public and free, yet unusable at digital speed. Sika fixes the last mile.

## Why now, why us

Frontier models can finally read a French statistical bulletin and structure its tables reliably. The builder is an Economic Statistician Engineer who produced some of these very publications at INSEED and supervised World Bank-funded survey collection: he knows each table's layout, its footnote traps, and which numbers matter. This is domain expertise no other Build Week team has.

## Users

See `docs/PERSONAS.md`. Primary: applied researcher, economic journalist, fintech/credit analyst, statistics student. Secondary: policy advisor.

## Core user journeys

1. **Ask**: type a question in French or English -> answer in the same language with the exact figure(s), each cited (document, page), plus an auto-generated chart when the data is a series.
2. **Explore**: browse the indicator catalog (what data exists, for where, over which period) to discover what can be asked.
3. **Brief**: pick a topic (inflation, microfinance, industrial production) -> receive a structured one-page economic note, every figure cited, ready to paste into a report.

## MVP features and acceptance criteria

| # | Feature | Acceptance criteria |
|---|---------|---------------------|
| F1 | Cited Q&A (`/ask`) | Answer contains only figures present in DB; each carries (source_doc, p. N); missing data acknowledged honestly; FR question -> FR answer, EN -> EN. |
| F2 | Auto charts | Any time-series result renders a Plotly chart titled and readable; no chart for single points. |
| F3 | Brief generator (`/brief`) | Given topic + geography, returns a structured note (situation, dynamics, outlook) with >= 5 cited figures when data allows; states data gaps. |
| F4 | Indicator catalog (`/indicators`) | Lists every indicator with geography, period coverage, observation count; reachable from the UI. |
| F5 | Corpus | >= 10 official publications ingested; >= 300 validated observations; each spot-checkable against its PDF page. |
| F6 | Deployed demo | Public URL; cold-start under 30 s; graceful errors; works on mobile width. |

## Stretch (only if MVP is done and stable)

Public read-only REST API with docs page; CSV export of any answer's data; more UEMOA countries; a "how was this number produced" provenance popover.

## Non-goals (4 days: discipline)

No user accounts. No real-time scraping. No fine-tuning. No mobile app. No coverage promises beyond ingested documents. No forecasting (we serve official numbers, we do not predict).

## Demo success criteria

The 8 golden questions in `docs/BACKLOG.md` answer correctly, with citations and charts, in under 15 seconds each, on the deployed URL, in one unedited take.

## Judging alignment

- **Tool application**: Codex builds the project (documented sessions, commit history); GPT-5.6 powers extraction, routing, answering, and brief writing: the product is impossible without it.
- **Design**: sober dark/amber UI, citations as first-class UI elements, honest empty states. Trust is the design language.
- **Impact**: 8 UEMOA countries, 140M people, one shared central bank; every ingested publication multiplies reusable public data. Direct users: researchers, press, fintech, students.
- **Novelty**: not another chatbot; a provenance-first liberation layer over official African statistics, built by an insider of the statistical system.

## Risks

| Risk | Mitigation |
|------|------------|
| Extraction errors poison answers | Confidence thresholds + validation script + manual spot-check protocol (DATA_SPEC). |
| Demo-day failure | Golden-question QA on deployed URL; seeded DB shipped with deploy; fallback local run recorded in video. |
| API cost/latency | Small corpus, cached extraction (run once), temperature 0, short prompts. |
| Scope creep | Non-goals list above is contractual. |
