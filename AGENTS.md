# AGENTS.md — Instructions for AI coding agents (Codex)

Read this file before any work session. It defines the project context, conventions, and boundaries.

## What this project is

Sika makes official West African economic statistics (INSEED Togo, BCEAO, WAEMU) queryable in natural language. Pipeline: official PDFs -> structured SQLite observations with full provenance -> FastAPI backend -> chat UI with charts and one-click economic briefs. Built for OpenAI Build Week 2026 (deadline: July 21, 5:00 pm PT). Solo builder: Mayo Kadanga, Economic Statistician Engineer (ISE), Lomé.

Read next: `docs/PRD.md` (what to build and why), `docs/DATA_SPEC.md` (data rules), `docs/BACKLOG.md` (current tickets).

## Repo map

```
pipeline/extract.py   PDF -> observations + passages (OpenAI API structured extraction)
api/main.py           FastAPI: /ask, /brief, /indicators + serves app/
app/index.html        Single-file chat UI (vanilla JS + Plotly CDN)
data/raw/             Source PDFs (NOT committed; see .gitignore)
data/processed/       sika.db (SQLite; NOT committed; regenerate from pipeline)
docs/                 PRD, personas, data spec, backlog, architecture, UI spec
```

## Commands

```bash
pip install -r requirements.txt
cp .env.example .env                      # then add OPENAI_API_KEY (never commit .env)
python pipeline/extract.py data/raw/      # ingest all PDFs
python pipeline/extract.py data/raw/x.pdf # ingest one PDF
uvicorn api.main:app --reload             # serve API + UI on :8000
```

## Hard rules

1. **Never invent a number.** Every figure shown to a user must exist in the `observations` table and carry `source_doc` + `source_page`. If data is missing, the answer says so plainly. This is the product's core promise; breaking it disqualifies the work.
2. **Never commit secrets.** `.env` stays local. If you touch config, update `.env.example` with placeholder values only.
3. **SELECT-only SQL** from the LLM router. Validate before executing. Never interpolate user text into SQL directly.
4. **No new frameworks.** Stack is frozen: Python/FastAPI/SQLite/vanilla JS/Plotly. New pip packages only with a one-line justification in the commit message. No React, no Docker, no ORM, no auth.
5. **UI language is French first**, English accepted in questions and answered in kind. Code, comments, commits: English.
6. **Small commits, imperative messages** (`Add period normalization for quarterly labels`). Commit after each working increment; the commit history is part of the Build Week submission evidence.
7. **Don't refactor working code for style** in the final 48 hours; only fix, harden, and polish what the demo exercises.

## Code conventions

- Python: type hints on public functions, docstrings only where intent is not obvious, no function over ~40 lines, f-strings, pathlib over os.path.
- Errors: user-facing endpoints must never 500 on bad input; return a structured message the UI can display. The UI must handle: empty DB, API down, slow response (loading state), no-data answers.
- LLM calls: always `response_format={"type": "json_object"}` for structured tasks; parse defensively; on parse failure return an empty result, never crash.
- Keep `app/index.html` a single file. No build step.

## Definition of done (any ticket)

- Runs locally via the commands above with no traceback.
- Golden questions in `docs/BACKLOG.md` (section: Golden questions) still pass.
- Citations render for every numeric claim.
- Committed and pushed.

## Testing quick check

```bash
python -c "import sqlite3; c=sqlite3.connect('data/processed/sika.db'); print(c.execute('SELECT COUNT(*), COUNT(DISTINCT source_doc) FROM observations').fetchone())"
curl -s localhost:8000/indicators | head -c 400
curl -s -X POST localhost:8000/ask -H 'Content-Type: application/json' -d '{"question":"Quelle est l inflation au Togo en 2025 ?"}' | head -c 600
```
