"""Sika ingestion pipeline: official PDFs -> structured, sourced observations.

Usage:
    python pipeline/extract.py data/raw/            # process every PDF
    python pipeline/extract.py data/raw/foo.pdf     # process one file
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pdfplumber
from dotenv import load_dotenv
from openai import BadRequestError, OpenAI

load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip()
client = OpenAI(api_key=API_KEY, base_url=BASE_URL or None)
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6")
DB_PATH = os.getenv("SIKA_DB", "data/processed/sika.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    indicator TEXT NOT NULL,          -- normalized name, e.g. 'inflation_rate_yoy'
    indicator_label TEXT NOT NULL,    -- human label, e.g. 'Inflation (glissement annuel)'
    geography TEXT NOT NULL,          -- e.g. 'Togo', 'UEMOA'
    period TEXT NOT NULL,             -- ISO-ish: '2025', '2025-Q3', '2025-11'
    value REAL NOT NULL,
    unit TEXT NOT NULL,               -- '%', 'milliards FCFA', 'index'
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

EXTRACT_PROMPT = """You are a statistical data extraction engine for official West African economic publications (INSEED Togo, BCEAO, WAEMU). From the page content below (raw text plus any detected tables), extract EVERY quantitative economic observation.

Return strict JSON: {"observations": [{"indicator": "...", "indicator_label": "...", "geography": "...", "period": "...", "value": 0.0, "unit": "...", "confidence": 0.0}]}

Rules:
- indicator: snake_case English canonical name (inflation_rate_yoy, industrial_production_index, credit_to_economy, gdp_growth, deposits_microfinance, ...).
- indicator_label: keep the original French label.
- period: normalize to '2025', '2025-Q3' or '2025-11'.
- value: number only. Convert '1 234,5' to 1234.5.
- Skip page numbers, footnote markers, and non-economic figures.
- confidence: your certainty the triple (indicator, period, value) is correct.
- If nothing extractable, return {"observations": []}.

PAGE CONTENT:
"""


def page_payload(page) -> str:
    text = page.extract_text() or ""
    tables = page.extract_tables() or []
    rendered = "\n\n".join(
        "\n".join(" | ".join(c or "" for c in row) for row in t) for t in tables
    )
    return (text + "\n\nTABLES:\n" + rendered)[:12000]


def parse_observations(content: str | None) -> list[dict]:
    if not content:
        return []
    cleaned = content.strip()
    fence = chr(96) * 3
    if cleaned.startswith(fence):
        cleaned = cleaned.removeprefix(fence).strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()
        cleaned = cleaned.removesuffix(fence).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            return []
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return []
    observations = parsed.get("observations", []) if isinstance(parsed, dict) else []
    return observations if isinstance(observations, list) else []


def extract_page(payload: str) -> list[dict]:
    kwargs = {
        "model": MODEL,
        "messages": [{"role": "user", "content": EXTRACT_PROMPT + payload}],
    }
    try:
        resp = client.chat.completions.create(
            **kwargs, response_format={"type": "json_object"}
        )
    except BadRequestError:
        print("  compatibility endpoint rejected JSON mode; retrying without it")
        resp = client.chat.completions.create(**kwargs)
    try:
        content = resp.choices[0].message.content
    except (AttributeError, IndexError):
        return []
    return parse_observations(content)

def process_pdf(pdf_path: Path, con: sqlite3.Connection) -> int:
    count = 0
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            payload = page_payload(page)
            if len(payload.strip()) < 40:
                continue
            con.execute(
                "INSERT INTO passages (source_doc, page, text) VALUES (?, ?, ?)",
                (pdf_path.name, i, payload[:4000]),
            )
            for ob in extract_page(payload):
                try:
                    con.execute(
                        """INSERT INTO observations
                           (indicator, indicator_label, geography, period,
                            value, unit, source_doc, source_page, confidence)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            ob["indicator"], ob["indicator_label"],
                            ob.get("geography", "Togo"), ob["period"],
                            float(ob["value"]), ob.get("unit", ""),
                            pdf_path.name, i, float(ob.get("confidence", 1.0)),
                        ),
                    )
                    count += 1
                except (KeyError, TypeError, ValueError):
                    continue
            con.commit()
            print(f"  page {i}: total {count} observations")
    return count


def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw")
    pdfs = [target] if target.is_file() else sorted(target.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"No PDFs found in {target}")
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    for pdf in pdfs:
        print(f"Processing {pdf.name} ...")
        n = process_pdf(pdf, con)
        print(f"  -> {n} observations")
    con.close()


if __name__ == "__main__":
    main()
