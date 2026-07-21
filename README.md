# Sika

**Ask West Africa's economy anything. Get the official answer, cited to the exact page.**

West Africa's official economic statistics are rigorous, public, and effectively unusable. They live in PDF bulletins published by INSEED Togo, the BCEAO and other WAEMU institutions, where finding a single number means downloading a document and scanning tables page by page. Sika turns that corpus into a queryable, citable database and puts a natural-language interface in front of it.

Every figure Sika returns carries its source document and page. If the data is not in the corpus, Sika says so instead of guessing.

Built for OpenAI Build Week 2026.

---

## What it does

- **Ask in French or English.** "Quelle est l'évolution de l'inflation au Togo ?" or "What is the latest industrial production index?"
- **Every number is cited.** Document and page appear inline as footnotes and again in a source panel. Hovering any chart point shows its provenance.
- **Charts are generated on the fly** from whatever series the question resolves to.
- **One-click economic briefs** synthesize the available observations on a theme, with the same citation discipline.
- **It refuses to invent.** Ask for a 2030 GDP forecast, or a month that isn't in the corpus, and Sika returns an honest empty answer rather than a plausible number.

## Current corpus

**1,051 observations** across **240 indicators**, extracted from **9 official publications**:

| Source | Observations |
|---|---|
| `inseed_ihpc_2026-06.pdf` — Consumer price index, June 2026 | 173 |
| `inseed_bulletin_mensuel_2025-09.pdf` — Monthly statistical bulletin | 164 |
| `inseed_pib_estimations_2025.pdf` — GDP estimates 2025 | 141 |
| `inseed_ipi_mensuel_2015-2026.xlsx` — Industrial production, monthly 2015–2026 | 135 |
| `bceao_politique_monetaire_2023-06.pdf` — BCEAO monetary policy report | 128 |
| `inseed_ihpc_2026-05.pdf` — Consumer price index, May 2026 | 122 |
| `inseed_comptes_trimestriels_2025-T4.pdf` — Quarterly accounts Q4 2025 | 99 |
| `inseed_ica_services_2026-T1.xlsx` — Services turnover index Q1 2026 | 45 |
| `inseed_ipi_trimestriel_2025-T4.xlsx` — Industrial price index Q4 2025 | 44 |

The seeded database ships with the repository (`data/processed/sika.db`), so the app runs immediately without re-running extraction.

---

## Setup

Requires Python 3.12+.

```bash
git clone https://github.com/mayonorris/sika.git
cd sika
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

Run the app:

```bash
uvicorn api.main:app --reload
```

Open http://127.0.0.1:8000. **No API key is required.** Without one, Sika serves deterministic, fully cited answers computed directly from the database — the same numbers, the same citations, without LLM-composed prose.

### Optional: enable LLM-composed answers

Create a `.env`:

```
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-5.6
SIKA_DB=data/processed/sika.db
```

The API client is provider-agnostic. Setting `OPENAI_BASE_URL` points it at any OpenAI-compatible endpoint.

### Optional: re-run extraction

```bash
python pipeline/download_sources.py     # fetch source documents into data/raw/
python pipeline/extract.py data/raw/    # PDFs, LLM-assisted (requires API key)
python pipeline/extract_xlsx.py         # Excel workbooks, deterministic
python pipeline/validate.py             # QA report
python pipeline/spot_check.py           # random sample for manual verification
```

---

## How Codex and GPT-5.6 built this

Sika was built during Build Week using **Codex running GPT-5.6**, working from specifications rather than ad-hoc prompts. The 26-commit history is the record.

**The method: specs first, then tickets.** Before writing code, I wrote the documents Codex would work from:

- `AGENTS.md` — the agent contract Codex reads at the start of every session. Hard rules (never invent a number, SELECT-only SQL, no new frameworks mid-build, French-first UI, small imperative commits), the repo map, exact commands, and the definition of done.
- `docs/PRD.md` — problem statement, user journeys, MVP features with acceptance criteria, explicit non-goals.
- `docs/PERSONAS.md` — five users, each with a pain point, a sample question, and a "would abandon if" condition that drove concrete design decisions.
- `docs/DATA_SPEC.md` — table schemas, the canonical indicator registry, French number-format normalization rules, the confidence policy, and a three-tier QA protocol.
- `docs/BACKLOG.md` — eighteen tickets, each with a Codex seed prompt and its own definition of done.

Codex then shipped against those tickets. Representative work:

- **The extraction pipeline** (`pipeline/extract.py`) — page-by-page PDF parsing with pdfplumber, LLM-assisted structuring into `(indicator, period, value, unit, source_doc, source_page, confidence)` tuples, with `source_page` validated against page markers so a citation can never drift.
- **Free-tier resilience** — when the runtime provider turned out to enforce a hard daily request cap rather than a per-minute one, Codex reworked the pipeline around it: three-page request batching, page-level resume tracking, priority file ordering, and exponential backoff. That constraint shaped the architecture more than any design decision I made up front.
- **Deterministic Excel ingestion** (`pipeline/extract_xlsx.py`) — three source URLs turned out to serve `.xlsx` workbooks behind PDF-looking links, detected by ZIP magic bytes. Codex built a separate non-LLM path for them. It produced the cleanest data in the corpus: eleven years of monthly industrial production with zero transcription error.
- **The no-API fallback** — cited answers, series statistics and charts computed directly from SQLite when no LLM is configured. This is why the deployed demo works without burning API quota.
- **A real data bug** — during review, GPT-5.6 diagnosed that `inflation_rate_yoy` was conflating the national headline index with 74 COICOP sub-category rows sharing the same indicator code. Charts zigzagged between unrelated series and the answer reported a "maximum" drawn from an unrelated category. The fix (headline filtering plus per-period deduplication) corrected an inflation figure that had been silently wrong, and three independent extractions across two documents now corroborate the right one.
- **Honest empty states** — point-in-time questions ("inflation en novembre 2025") were being silently ignored and answered with whatever months happened to exist. Now they return nothing when the corpus has nothing.

**Codex session ID:** `019f7414-fa13-7ab0-a63c-7f22a2c289cb`

---

## Architecture

```
sika/
├── AGENTS.md              # agent contract, read by Codex every session
├── api/main.py            # FastAPI: /ask, /brief, /sources, static frontend
├── app/index.html         # single-file frontend, Plotly via CDN
├── pipeline/
│   ├── download_sources.py
│   ├── extract.py         # PDF -> observations (LLM-assisted)
│   ├── extract_xlsx.py    # Excel -> observations (deterministic)
│   ├── validate.py        # QA report
│   └── spot_check.py      # random sample for manual verification
├── data/processed/sika.db # seeded database, ships with the repo
├── docs/                  # PRD, personas, data spec, backlog, video script
└── tests/test_smoke.py
```

**Stack:** Python 3.12, FastAPI, SQLite, pdfplumber, openpyxl, pandas, Plotly.js, vanilla JS.

Two design decisions worth naming. First, **provenance is a column, not a feature** — `source_doc` and `source_page` are NOT NULL on every observation, so an uncited number cannot exist in the database. Second, **the LLM never sees raw numbers at answer time** — it composes prose around rows retrieved by SQL, so it cannot alter a figure.

A note on the `confidence` column: it records how certain the extractor was that it read a number correctly off a page. It is not a statistical confidence interval, and it is never surfaced to users as one.

---

## Tests

```bash
pytest
```

---

## What's next

- Extend the corpus to all eight WAEMU member states
- Ingest BCEAO monetary and banking series systematically
- Scheduled ingestion so new publications are queryable the day they are released
- A public API for researchers and newsrooms

---

## Author

**Mayo Takémsi Norris Kadanga** — statistician-economist, Lomé, Togo.
Two years at INSEED, the national statistics office that publishes most of the documents in this corpus. Sika exists because I watched researchers and journalists hunt for these numbers page by page.

[Portfolio](https://mayokadanga-portfolio.vercel.app/en) · [GitHub](https://github.com/mayonorris)
