"""Validate the Sika observations database against docs/DATA_SPEC.md."""

import argparse
import os
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, pstdev

DEFAULT_DB_PATH = os.getenv("SIKA_DB", "data/processed/sika.db")
MIN_ROWS_PER_DOCUMENT = 5
OUTLIER_Z_THRESHOLD = 4.0
PERIOD_PATTERN = re.compile(r"^\d{4}(?:-Q[1-4]|-(?:0[1-9]|1[0-2]))?$")
VALID_UNITS = {
    "%",
    "points",
    "milliards FCFA",
    "millions FCFA",
    "index",
    "unites",
}


@dataclass(frozen=True)
class Finding:
    severity: str
    check: str
    message: str


@dataclass
class ValidationReport:
    total_rows: int
    rows_by_document: list[tuple[str, int]]
    findings: list[Finding]

    @property
    def hard_failures(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "FAIL"]


def check_row_counts(rows: list[tuple[str, int]]) -> list[Finding]:
    return [
        Finding("WARN", "rows/document", f"{source_doc}: {count} rows (< 5)")
        for source_doc, count in rows
        if count < MIN_ROWS_PER_DOCUMENT
    ]


def check_outliers(con: sqlite3.Connection) -> list[Finding]:
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    rows = con.execute(
        """SELECT id, indicator, value, source_doc, source_page
           FROM observations ORDER BY indicator, id"""
    ).fetchall()
    for row in rows:
        grouped[row["indicator"]].append(row)

    findings = []
    for indicator, indicator_rows in grouped.items():
        values = [float(row["value"]) for row in indicator_rows]
        deviation = pstdev(values)
        if not deviation:
            continue
        mean = fmean(values)
        for row, value in zip(indicator_rows, values):
            z_score = (value - mean) / deviation
            if abs(z_score) > OUTLIER_Z_THRESHOLD:
                location = f"{row['source_doc']}, p. {row['source_page']}"
                message = f"{indicator}: {value:g}, z={z_score:.2f} ({location})"
                findings.append(Finding("WARN", "outlier", message))
    return findings


def check_periods(con: sqlite3.Connection) -> list[Finding]:
    rows = con.execute("SELECT id, period FROM observations ORDER BY id").fetchall()
    return [
        Finding("FAIL", "period", f"row {row['id']}: {row['period']!r}")
        for row in rows
        if not isinstance(row["period"], str)
        or not PERIOD_PATTERN.fullmatch(row["period"])
    ]


def check_units(con: sqlite3.Connection) -> list[Finding]:
    rows = con.execute(
        "SELECT unit, COUNT(*) AS count FROM observations GROUP BY unit ORDER BY unit"
    ).fetchall()
    return [
        Finding("FAIL", "unit", f"{row['unit']!r}: {row['count']} rows")
        for row in rows
        if row["unit"] not in VALID_UNITS
    ]


def check_duplicates(con: sqlite3.Connection) -> list[Finding]:
    rows = con.execute(
        """SELECT indicator, geography, period, source_doc, COUNT(*) AS count
           FROM observations
           GROUP BY indicator, geography, period, source_doc
           HAVING COUNT(*) > 1
           ORDER BY source_doc, indicator, geography, period"""
    ).fetchall()
    return [
        Finding(
            "FAIL",
            "duplicate",
            f"{row['indicator']} | {row['geography']} | {row['period']} | "
            f"{row['source_doc']}: {row['count']} rows",
        )
        for row in rows
    ]


def validate_database(con: sqlite3.Connection) -> ValidationReport:
    con.row_factory = sqlite3.Row
    total_rows = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    rows = con.execute(
        """SELECT source_doc, COUNT(*) AS count FROM observations
           GROUP BY source_doc ORDER BY source_doc"""
    ).fetchall()
    rows_by_document = [(row["source_doc"], row["count"]) for row in rows]
    findings = check_row_counts(rows_by_document)
    if total_rows == 0:
        findings.append(Finding("FAIL", "database", "observations table is empty"))
    findings.extend(check_outliers(con))
    findings.extend(check_periods(con))
    findings.extend(check_units(con))
    findings.extend(check_duplicates(con))
    return ValidationReport(total_rows, rows_by_document, findings)


def print_report(report: ValidationReport, database: Path) -> None:
    print("Sika data validation")
    print(f"Database: {database}")
    print(f"Observations: {report.total_rows}")
    print("\nRows per document:")
    if not report.rows_by_document:
        print("  [FAIL] no documents")
    for source_doc, count in report.rows_by_document:
        status = "WARN" if count < MIN_ROWS_PER_DOCUMENT else "PASS"
        print(f"  [{status}] {source_doc}: {count}")

    print("\nFindings:")
    if not report.findings:
        print("  none")
    for finding in report.findings:
        print(f"  [{finding.severity}] {finding.check}: {finding.message}")
    warning_count = sum(finding.severity == "WARN" for finding in report.findings)
    print(
        f"\nSummary: {len(report.hard_failures)} hard failures, "
        f"{warning_count} warnings"
    )
    print("Result: FAIL" if report.hard_failures else "Result: PASS")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the Sika observations DB")
    parser.add_argument("database", nargs="?", default=DEFAULT_DB_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    database = Path(args.database)
    if not database.is_file():
        print(f"Sika data validation\nDatabase: {database}\n[FAIL] database not found")
        return 1
    try:
        with sqlite3.connect(database) as con:
            report = validate_database(con)
    except sqlite3.Error as exc:
        print(f"Sika data validation\nDatabase: {database}\n[FAIL] {exc}")
        return 1
    print_report(report, database)
    return 1 if report.hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
