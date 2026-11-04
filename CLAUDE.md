# CLAUDE.md

@AGENTS.md

`AGENTS.md` (imported above) is the project contract: its hard rules, source-of-truth
order, working sequence and definition of done apply unchanged. This file only adds
Linux/macOS (bash) equivalents of its Windows PowerShell commands.

## Commands (Linux/macOS)

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn api.main:app --reload

# Default suite: no provider key is needed and none may be used.
.venv/bin/python -m pytest -q
.venv/bin/python pipeline/validate.py
.venv/bin/python pipeline/spot_check.py

# Trust Core only: explicit separate database; does not load .env or use SIKA_DB.
export SIKA_DATABASE_URL='sqlite:///data/processed/sika_v2.db'
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m pytest tests/test_schema_migrations.py -q

# S0.3: first import requires an empty, versioned target. An exact replay is safe.
.venv/bin/python -m sika.migrate_inflation --target data/processed/sika_v2.db --report docs/reports/S0_3_inflation_reconciliation.json
.venv/bin/python -m pytest tests/test_migrate_inflation.py -q

# Technical QA: records evidence; PASS alone never authorizes publication.
.venv/bin/python -m sika.validate --target data/processed/sika_v2.db --legacy data/processed/sika.db --report docs/reports/S0_4_2_inflation_quality.json
.venv/bin/python -m pytest tests/test_quality.py -q

# S0.4.2: identity only. Review requires verified sources and an external allowlist.
.venv/bin/python -m sika.review identity
.venv/bin/python -m pytest tests/test_publication.py -q
.venv/bin/python -m pytest tests/test_gate_adversarial.py tests/test_review_cli.py -q

# S0.4.3: read-only review aid. Exit 0 is packet generation, NOT certification.
.venv/bin/python -m sika.source_review --target data/processed/sika_v2.db --legacy data/processed/sika.db --report docs/reports/local/S0_4_3_source_review.json
.venv/bin/python -m sika.source_review --target data/processed/sika_v2.db --legacy data/processed/sika.db --summary-only --report docs/reports/S0_4_3_source_review.json
.venv/bin/python -m pytest tests/test_source_review.py tests/test_source_tables.py -q

# S0.5.1: pure query preparation on synthetic fixtures; no public execution.
.venv/bin/python -m pytest tests/test_query.py -q

# S0.5.2: protected execution on synthetic publication fixtures, not MVP routes.
.venv/bin/python -m pytest tests/test_publication_read.py tests/test_query_execution.py -q

# S0.5.3: deterministic FR/EN interpretation and isolated fixture HTTP integration.
.venv/bin/python -m pytest tests/test_interpretation.py tests/test_query_api.py -q

# Isolated manual S0.5.3 sandbox; new synthetic DB each launch, never real targets.
.venv/bin/python -m dev.fixture_sandbox --port 8001
.venv/bin/python -m pytest tests/test_fixture_sandbox.py -q

# S0.6: /health/live, /health/ready and structured JSON logs on stderr.
.venv/bin/python -m pytest tests/test_health.py tests/test_observability.py -q
curl -s localhost:8000/health/ready

# Optional source ingestion. PDF extraction requires a configured provider key;
# deterministic Excel and BCEAO paths do not use the LLM for numeric values.
.venv/bin/python pipeline/extract.py data/raw/one.pdf
.venv/bin/python pipeline/extract_xlsx.py
.venv/bin/python pipeline/extract_bceao.py
```

`pipeline/extract.py` creates its provider client lazily, so importing it (and
`tests/test_extract.py`) needs no `OPENAI_API_KEY`. The `set OPENAI_API_KEY=test`
prefix in AGENTS.md is no longer required; it is harmless if kept.

## Testing quick check (Linux/macOS)

```bash
.venv/bin/python -m pytest -q
.venv/bin/python pipeline/validate.py
.venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/processed/sika.db'); print(c.execute('SELECT COUNT(*), COUNT(DISTINCT source_doc) FROM observations').fetchone())"
sha256sum data/processed/sika.db   # must be identical before and after any work
```

`pipeline/validate.py` currently exits 1 on the committed database because of
pre-existing corpus failures (see AGENTS.md). CI does not treat them as resolved: it
requires the exact `[FAIL]` lines to match `docs/reports/legacy_validation_baseline.txt`.
A new failure is a regression. When a failure is genuinely fixed through the proper
review process, remove its line from the baseline in the same commit.

## Continuous integration

`.github/workflows/ci.yml` runs on Python 3.12 without any provider key: install,
`pytest -q`, an Alembic `upgrade head` / `downgrade base` / `upgrade head` round trip
on a temporary SQLite database, the legacy validation baseline check, and a final
check that `data/processed/sika.db` is byte-identical.
