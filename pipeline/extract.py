"""Sika ingestion pipeline: official PDFs -> structured, sourced observations.

Usage:
    python pipeline/extract.py data/raw/            # process every PDF
    python pipeline/extract.py data/raw/foo.pdf     # process one file
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pdfplumber
from dotenv import load_dotenv
from openai import BadRequestError, OpenAI, RateLimitError

load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip()
client = OpenAI(api_key=API_KEY, base_url=BASE_URL or None)
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6")
DB_PATH = os.getenv("SIKA_DB", "data/processed/sika.db")
PAGE_BATCH_SIZE = 3
MAX_BATCH_CHARS = 30000
PRIORITY_FILES = (
    "inseed_ihpc_2026-05.pdf",
    "inseed_comptes_trimestriels_2025-T4.pdf",
    "inseed_pib_estimations_2025.pdf",
    "inseed_bulletin_mensuel_2025-09.pdf",
)

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

Return strict JSON: {"observations": [{"indicator": "...", "indicator_label": "...", "geography": "...", "period": "...", "value": 0.0, "unit": "...", "source_page": 1, "confidence": 0.0}]}

Rules:
- indicator: snake_case English canonical name (inflation_rate_yoy, industrial_production_index, credit_to_economy, gdp_growth, deposits_microfinance, ...).
- indicator_label: keep the original French label.
- period: normalize to '2025', '2025-Q3' or '2025-11'.
- value: number only. Convert '1 234,5' to 1234.5.
- source_page: integer from the === SOURCE PAGE N === marker containing the observation.
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


def create_completion(kwargs: dict[str, object], json_mode: bool) -> object:
    for retry in range(6):
        try:
            extra = {"response_format": {"type": "json_object"}} if json_mode else {}
            return client.chat.completions.create(**kwargs, **extra)
        except RateLimitError:
            if retry == 5:
                raise
            delay = min(15 * (2**retry), 120)
            print(f"  rate limited; retrying same request in {delay}s ({retry + 1}/5)")
            time.sleep(delay)


def extract_page(payload: str) -> list[dict]:
    kwargs = {
        "model": MODEL,
        "messages": [{"role": "user", "content": EXTRACT_PROMPT + payload}],
    }
    try:
        resp = create_completion(kwargs, json_mode=True)
    except BadRequestError:
        print("  compatibility endpoint rejected JSON mode; retrying without it")
        resp = create_completion(kwargs, json_mode=False)
    try:
        content = resp.choices[0].message.content
    except (AttributeError, IndexError):
        return []
    return parse_observations(content)

def batch_payload(pages: list[tuple[int, str]]) -> str:
    parts: list[str] = []
    remaining = MAX_BATCH_CHARS
    for index, (page_number, payload) in enumerate(pages):
        prefix = "" if index == 0 else "\n\n"
        marker = f"{prefix}=== SOURCE PAGE {page_number} ===\n"
        pages_left = len(pages) - index
        content_budget = max(0, remaining - len(marker)) // pages_left
        section = marker + payload[:content_budget]
        parts.append(section)
        remaining -= len(section)
    return "".join(parts)


def existing_passage_pages(con: sqlite3.Connection, source_doc: str) -> set[int]:
    rows = con.execute(
        "SELECT DISTINCT page FROM passages WHERE source_doc = ?", (source_doc,)
    ).fetchall()
    return {int(row[0]) for row in rows}


def insert_observations(
    con: sqlite3.Connection, source_doc: str,
    page_numbers: set[int], observations: list[dict],
) -> int:
    count = 0
    for ob in observations:
        try:
            source_page = int(ob["source_page"])
            if source_page not in page_numbers:
                continue
            con.execute(
                """INSERT INTO observations
                   (indicator, indicator_label, geography, period,
                    value, unit, source_doc, source_page, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ob["indicator"], ob["indicator_label"],
                    ob.get("geography", "Togo"), ob["period"],
                    float(ob["value"]), ob.get("unit", ""), source_doc,
                    source_page, float(ob.get("confidence", 1.0)),
                ),
            )
            count += 1
        except (KeyError, TypeError, ValueError):
            continue
    return count


def process_batch(
    con: sqlite3.Connection, pdf_path: Path, pages: list[tuple[int, str]],
) -> int:
    observations = extract_page(batch_payload(pages))
    page_numbers = {page_number for page_number, _ in pages}
    count = insert_observations(con, pdf_path.name, page_numbers, observations)
    con.executemany(
        "INSERT INTO passages (source_doc, page, text) VALUES (?, ?, ?)",
        [(pdf_path.name, page, payload[:4000]) for page, payload in pages],
    )
    con.commit()
    return count


def process_pdf(
    pdf_path: Path, con: sqlite3.Connection, skip_existing: bool = False,
) -> int:
    count = 0
    pending: list[tuple[int, str]] = []
    completed = existing_passage_pages(con, pdf_path.name) if skip_existing else set()
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            if page_number in completed:
                print(f"  page {page_number}: already processed, skipping")
                continue
            payload = page_payload(page)
            if len(payload.strip()) < 40:
                continue
            pending.append((page_number, payload))
            if len(pending) < PAGE_BATCH_SIZE:
                continue
            count += process_batch(con, pdf_path, pending)
            print(f"  pages {pending[0][0]}-{pending[-1][0]}: total {count} observations")
            pending = []
        if pending:
            count += process_batch(con, pdf_path, pending)
            print(f"  pages {pending[0][0]}-{pending[-1][0]}: total {count} observations")
    return count


def prioritize_pdfs(pdfs: list[Path]) -> list[Path]:
    ranks = {name: index for index, name in enumerate(PRIORITY_FILES)}
    return sorted(pdfs, key=lambda path: (ranks.get(path.name, len(ranks)), path.name))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract observations from PDFs")
    parser.add_argument("target", nargs="?", default="data/raw")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="skip PDF pages already present in the passages table",
    )
    parser.add_argument(
        "--priority",
        action="store_true",
        help="process demo-critical PDFs first",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    target = Path(args.target)
    pdfs = [target] if target.is_file() else sorted(target.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"No PDFs found in {target}")
    if args.priority:
        pdfs = prioritize_pdfs(pdfs)
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    for pdf in pdfs:
        print(f"Processing {pdf.name} ...")
        n = process_pdf(pdf, con, skip_existing=args.skip_existing)
        print(f"  -> {n} observations")
    con.close()


if __name__ == "__main__":
    main()
