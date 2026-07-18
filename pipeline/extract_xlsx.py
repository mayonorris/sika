"""Deterministic Sika ingestion for official INSEED Excel time series.

Usage:
    python pipeline/extract_xlsx.py data/raw/
    python pipeline/extract_xlsx.py data/raw/inseed_ipi_mensuel_2015-2026.xlsx
"""

import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()
DB_PATH = Path(os.getenv("SIKA_DB", "data/processed/sika.db"))

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


@dataclass(frozen=True)
class WorkbookSpec:
    indicator: str
    row_label: str
    period_overrides: tuple[tuple[int, str, float], ...] = ()


WORKBOOKS = {
    "inseed_ipi_mensuel_2015-2026.xlsx": WorkbookSpec(
        "industrial_production_index", "INDICE GLOBAL"
    ),
    "inseed_ipi_trimestriel_2025-T4.xlsx": WorkbookSpec(
        "industrial_producer_price",
        "INDICE GLOBAL",
        ((27, "2021-Q3", 0.75),),  # Source cell AB6 is mislabeled T3-22.
    ),
    "inseed_ica_services_2026-T1.xlsx": WorkbookSpec(
        "turnover_index_services", "GLOBAL"
    ),
}


def normalize_period(value: object) -> str | None:
    """Return a DATA_SPEC period for an Excel date or quarterly label."""
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return f"{value.year:04d}-{value.month:02d}"
    label = str(value).strip()
    match = re.fullmatch(r"T([1-4])[- /](\d{2}|\d{4})", label, re.IGNORECASE)
    if not match:
        return None
    year = int(match.group(2))
    year = year + 2000 if year < 100 else year
    return f"{year:04d}-Q{match.group(1)}"


def find_series(frame: pd.DataFrame, row_label: str) -> tuple[int, int]:
    """Locate the first requested label and its nearest preceding header row."""
    labels = frame.iloc[:, 0].astype(str).str.strip().str.upper()
    matches = frame.index[labels == row_label.upper()].tolist()
    if not matches:
        raise ValueError(f"Row label {row_label!r} not found")
    data_row = matches[0]
    for header_row in range(data_row - 1, -1, -1):
        if any(normalize_period(value) for value in frame.iloc[header_row, 1:]):
            return header_row, data_row
    raise ValueError(f"Period header not found above {row_label!r}")


def observations_from_sheet(
    frame: pd.DataFrame, spec: WorkbookSpec, source_doc: str, source_page: int
) -> list[tuple[object, ...]]:
    header_row, data_row = find_series(frame, spec.row_label)
    indicator_label = str(frame.iat[data_row, 0]).strip()
    overrides = {
        column: (period, confidence)
        for column, period, confidence in spec.period_overrides
    }
    observations = []
    for column in range(1, frame.shape[1]):
        period, confidence = overrides.get(
            column, (normalize_period(frame.iat[header_row, column]), 1.0)
        )
        value = pd.to_numeric(frame.iat[data_row, column], errors="coerce")
        if period is None or pd.isna(value):
            continue
        observations.append(
            (
                spec.indicator,
                indicator_label,
                "Togo",
                period,
                float(value),
                "index",
                source_doc,
                source_page,
                confidence,
            )
        )
    return observations


def upsert_observation(con: sqlite3.Connection, row: tuple[object, ...]) -> bool:
    key = (row[0], row[2], row[3], row[6])
    existing = con.execute(
        """SELECT id, indicator_label, value, unit, source_page, confidence
           FROM observations
           WHERE indicator = ? AND geography = ? AND period = ? AND source_doc = ?""",
        key,
    ).fetchone()
    if existing:
        current = (
            existing[1],
            float(existing[2]),
            existing[3],
            existing[4],
            float(existing[5]),
        )
        incoming = (row[1], float(row[4]), row[5], row[7], float(row[8]))
        if float(existing[5]) > float(row[8]) or current == incoming:
            return False
        con.execute(
            """UPDATE observations SET indicator_label = ?, value = ?, unit = ?,
               source_page = ?, confidence = ? WHERE id = ?""",
            (row[1], row[4], row[5], row[7], row[8], existing[0]),
        )
        return True
    con.execute(
        """INSERT INTO observations
           (indicator, indicator_label, geography, period, value, unit,
            source_doc, source_page, confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        row,
    )
    return True


def process_workbook(path: Path, con: sqlite3.Connection) -> int:
    spec = WORKBOOKS.get(path.name)
    if spec is None:
        raise ValueError(f"Unsupported workbook: {path.name}")
    sheets = pd.read_excel(path, sheet_name=None, header=None, engine="openpyxl")
    changed = 0
    for sheet_index, (sheet_name, frame) in enumerate(sheets.items(), start=1):
        rows = observations_from_sheet(frame, spec, path.name, sheet_index)
        changed += sum(upsert_observation(con, row) for row in rows)
        print(f"  sheet {sheet_index} ({sheet_name}): {len(rows)} observations")
    con.commit()
    return changed


def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw")
    workbooks = [target] if target.is_file() else sorted(target.glob("*.xlsx"))
    if not workbooks:
        sys.exit(f"No XLSX files found in {target}")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.executescript(SCHEMA)
        for workbook in workbooks:
            print(f"Processing {workbook.name} ...")
            changed = process_workbook(workbook, con)
            print(f"  -> {changed} inserted or updated")


if __name__ == "__main__":
    main()
