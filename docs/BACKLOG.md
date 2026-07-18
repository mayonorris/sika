# Backlog — 4 days to submission

Deadline: July 21, 5:00 pm PT (= July 22, 00:00 Lomé). Work top to bottom; a day's tickets are done before starting the next day's. Every ticket ends with a commit. "DoD" = definition of done (plus the global one in AGENTS.md).

## Day 1 (Fri Jul 18) — the pipeline proves itself

**T1. Repo live.** Create public GitHub repo `sika`, push scaffold, add `.gitignore` (`.env`, `data/raw/`, `data/processed/`, `__pycache__`). DoD: repo public, README renders.

**T2. Environment up.** `pip install -r requirements.txt`, `.env` filled, one 2-line smoke test of the OpenAI key. DoD: extract.py starts without import errors.

**T3. First ingestion.** Run `pipeline/extract.py` on 3 PDFs (1 INSEED conjoncture, 1 IHPC note, 1 BCEAO bulletin). Fix whatever breaks (pdfplumber quirks, JSON parse, unit noise). DoD: >= 40 observations across >= 2 documents; spot-check 5 rows against the PDFs: 5/5 correct.
> Codex seed: "Read AGENTS.md and docs/DATA_SPEC.md. Run the extraction pipeline on data/raw/, diagnose failures, and fix extract.py so observations conform to the spec. Show me a sample of 10 rows with their source pages."

**T4. Validation harness.** Build `pipeline/validate.py` implementing DATA_SPEC's QA protocol (counts, outliers, period regex, unit vocab, dedup report). DoD: runs clean on current DB; report readable in terminal.

**T5. Full corpus.** Ingest all 10+ PDFs; run validate; fix extractor rules the report exposes; re-run. DoD: >= 300 observations, >= 8 documents, validation clean, 10/10 manual spot-check.

## Day 2 (Sat Jul 19) — answers worth trusting

**T6. Harden /ask.** Router: tolerate accents/synonyms (inflation/IHPC/prix), map country names FR/EN, reject non-SELECT, add `confidence >= 0.5` filter, fallback to passage-only answer when SQL returns nothing. DoD: all 8 golden questions answered correctly with citations.

**T7. Chart correctness.** Series detection (>= 3 points, same indicator+geo), sorted periods, axis labels, unit in title, bar for single-year comparisons across geographies. DoD: charts for GQ1, GQ2, GQ5 look presentation-ready.

**T8. Brief quality.** `/brief`: enforce structure (title, 3 sections, executive summary), every figure cited, explicit data-gap sentence when < 5 relevant rows; add `?format=markdown` passthrough. DoD: briefs for "inflation" and "microfinance" read like a professional note.

**T9. Failure grace.** Empty DB message, OpenAI timeout -> friendly retry message, malformed question -> guidance, mobile-width layout check. DoD: kill the API mid-demo and the UI stays dignified.

**T10. Smoke tests.** `tests/test_smoke.py`: DB row count, /indicators 200, /ask on GQ1 returns citation pattern `(.*, p\. \d+)`. DoD: `pytest` green.

## Day 3 (Sun Jul 20) — the product looks the part

**T11. UI polish per docs/UI_SPEC.md.** Header tagline, example-question chips (the golden questions), citation styling (distinct chip after each figure), brief rendered as a card with copy button, loading state with progress text. DoD: side-by-side with UI_SPEC, no gaps.

**T12. Sources page.** `/sources` endpoint + UI section listing ingested documents (publisher, title, pages, observation count) linking provenance philosophy. DoD: reachable from header; earns the trust pitch in the video.

**T13. Deploy.** Backend + seeded `sika.db` on Render (or Railway); frontend served by FastAPI (single origin, zero CORS pain); custom start command runs on port from env. DoD: public URL passes all golden questions; cold start < 30 s documented.

**T14. Deployed QA.** Full golden-question pass on the public URL, mobile check, one friend-test (send URL to a colleague, watch them use it cold). DoD: notes filed, blockers fixed same day.

## Day 4 (Mon Jul 21) — the story wins

**T15. README final.** Pitch, architecture diagram (ASCII fine), quickstart, golden questions, **"Built with Codex" section**: how Codex was used per phase, with 2-3 session excerpts and the commit history as evidence. DoD: a judge can run the project from README alone.

**T16. Video (<= 3 min, unlisted YouTube).** Script in docs/VIDEO_SCRIPT.md (Lot 3): 20 s problem (PDF hunting, lived experience at INSEED), 90 s live demo (GQ flow: ask -> cited answer -> chart -> brief -> sources), 20 s architecture + Codex, 20 s impact + roadmap. DoD: uploaded, link works in private window.

**T17. Devpost submission.** Fill every field (docs/SUBMISSION.md draft), category choice, repo URL, video URL, deployed URL, screenshots. **Submit by 20:00 Lomé (buffer 4 h before deadline).** DoD: confirmation email received.

**T18. Freeze.** No commits after submission except README typos. Celebrate.

## Golden questions (the demo IS these)

1. Quelle est l'évolution de l'inflation au Togo depuis 2023 ? *(FR, series + chart)*
2. What is the latest industrial production index for Togo, and its year-on-year change? *(EN, point + citation)*
3. Quels sont les dépôts et crédits de la microfinance au Togo ? *(FR, multi-indicator)*
4. Compare le taux directeur de la BCEAO et l'inflation au Togo. *(FR, two series)*
5. Comment le chiffre d'affaires de l'industrie togolaise a-t-il évolué récemment ? *(FR, series + chart)*
6. What data do you have about Senegal? *(EN, catalog-style honest answer)*
7. Quel sera le PIB du Togo en 2030 ? *(FR, honest refusal: no forecasting)*
8. Génère un brief sur la microfinance au Togo. *(FR, brief feature)*

## Parking lot (post-hackathon, mention in "What's next")

Public API keys, CSV export, full 8-country corpus, automated monthly ingestion, provenance popovers, WhatsApp interface for journalists.
