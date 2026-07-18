"""Seed the database with SYNTHETIC fixture data for development WITHOUT the OpenAI API.

Usage:
    python pipeline/seed_fixtures.py

WARNING: fixture values are invented (plausible magnitudes only). They exist so the
API and UI can be developed and tested before real ingestion. Before any demo,
recording, or deployment: delete data/processed/sika.db and run the real pipeline
(pipeline/extract.py) on the official PDFs in data/raw/.
"""
import json
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("SIKA_DB", "data/processed/sika.db")
FIXTURES = Path("data/fixtures/observations.json")

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    indicator TEXT NOT NULL,
    indicator_label TEXT NOT NULL,
    geography TEXT NOT NULL,
    period TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    source_doc TEXT NOT NULL,
    source_page INTEGER,
    confidence REAL DEFAULT 1.0
);
CREATE TABLE IF NOT EXISTS passages (
    id INTEGER PRIMARY KEY,
    source_doc TEXT NOT NULL,
    page INTEGER NOT NULL,
    text TEXT NOT NULL
);
"""


def main() -> None:
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    con.execute("DELETE FROM observations WHERE source_doc LIKE 'FIXTURE%'")
    con.execute("DELETE FROM passages WHERE source_doc LIKE 'FIXTURE%'")
    for ob in data["observations"]:
        con.execute(
            """INSERT INTO observations
               (indicator, indicator_label, geography, period, value, unit,
                source_doc, source_page, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ob["indicator"], ob["indicator_label"], ob["geography"], ob["period"],
             ob["value"], ob["unit"], ob["source_doc"], ob["source_page"], ob["confidence"]),
        )
    for p in data["passages"]:
        con.execute("INSERT INTO passages (source_doc, page, text) VALUES (?, ?, ?)",
                    (p["source_doc"], p["page"], p["text"]))
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    con.close()
    print(f"Seeded {n} observations into {DB_PATH}")
    print("=" * 60)
    print("WARNING: SYNTHETIC DEV DATA ONLY. Replace with real ingestion")
    print("(pipeline/extract.py on data/raw/) before demo or deployment.")
    print("=" * 60)


if __name__ == "__main__":
    main()
