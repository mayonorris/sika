"""Smoke tests for the database validation CLI."""

import sqlite3
from pathlib import Path

import pytest

from pipeline import validate

SCHEMA = """
CREATE TABLE observations (
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
"""


def create_database(path: Path, invalid: bool = False) -> None:
    periods = ["2024", "2025-Q1", "2025-01", "2025-02", "2025-03"]
    with sqlite3.connect(path) as con:
        con.executescript(SCHEMA)
        for index, period in enumerate(periods, start=1):
            con.execute(
                """INSERT INTO observations
                   (indicator, indicator_label, geography, period, value, unit,
                    source_doc, source_page, confidence)
                   VALUES (?, 'IHPC', 'Togo', ?, ?, ?, 'source.pdf', ?, 1.0)""",
                (
                    "cpi_index",
                    "2025-13" if invalid and index == 1 else period,
                    float(index),
                    "unknown" if invalid and index == 1 else "index",
                    index,
                ),
            )
        if invalid:
            con.execute(
                """INSERT INTO observations
                   (indicator, indicator_label, geography, period, value, unit,
                    source_doc, source_page, confidence)
                   SELECT indicator, indicator_label, geography, period, value, unit,
                          source_doc, source_page, confidence
                   FROM observations WHERE id = 2"""
            )


def test_validate_smoke_passes_clean_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "clean.db"
    create_database(database)

    exit_code = validate.main([str(database)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[PASS] source.pdf: 5" in output
    assert "Result: PASS" in output


def test_validate_returns_one_for_hard_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "invalid.db"
    create_database(database, invalid=True)

    exit_code = validate.main([str(database)])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[FAIL] period" in output
    assert "[FAIL] unit" in output
    assert "[FAIL] duplicate" in output
    assert "Result: FAIL" in output
