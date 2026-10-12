"""Read supported cells from two checksum-bound IHPC PDFs, never persist facts.

This is deliberately not a generic PDF extractor. The inspected table layouts,
French headers and exact category registry define its limited support. Weights,
unreviewed categories and narrative claims are not admitted by this parser.
"""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
from io import BytesIO
from pathlib import Path
import re

import pdfplumber

from sika.inflation_mapping import CATEGORY_ALIASES, GEOGRAPHIES, PROFILES, normalize

TABLE_PARSER_VERSION = "ihpc-source-tables-1.0"

# Pin these independently: changing an ingestion profile cannot extend this review.
_DIGESTS = {
    "inseed_ihpc_2026-05.pdf": "954080aafa7b065fc9be801a8d6651da79bfe3f47edb024e5529f91d03fb0e93",
    "inseed_ihpc_2026-06.pdf": "792b218f6159ecd43d5fa9fbea7d79552fa5f458841d79d4e7be0e55ed8dfb30",
}
SUPPORTED_HASHES = dict(_DIGESTS)
_MONTHS = {"janv": 1, "fevr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6,
           "juil": 7, "aout": 8, "sept": 9, "oct": 10, "nov": 11, "dec": 12}
_NUMBER = re.compile(r"-?[0-9]+(?:[,.][0-9]+)?")
_RATE_HEADERS = ("1mois", "3mois", "12mois")
_RATE_CONCEPTS = ("inflation_rate_mom", "inflation_rate_3m", "inflation_rate_yoy")


@dataclass(frozen=True)
class _TableSpec:
    page: int
    table_index: int
    table_number: int
    label_column: int
    month_row: int
    row_count: int
    countries: bool = False


def _specs(doc: str) -> tuple[_TableSpec, ...]:
    june = doc == "inseed_ihpc_2026-06.pdf"
    return (
        _TableSpec(1, 1, 1, 1, 2, 15), _TableSpec(2, 0, 1, 1, 2, 21),
        _TableSpec(5, 0, 2, 0, 1, 11, True), _TableSpec(5, 1, 3, 0, 0, 16),
        _TableSpec(6, 1, 5, 0, 1, 31 if june else 29),
        _TableSpec(7, 0, 5, 0, 1, 21 if june else 23),
    )


def _period(header: str) -> str:
    match = re.fullmatch(r"([a-z]+)-([0-9]{2})", normalize(header))
    if not match or match[1] not in _MONTHS:
        raise ValueError(f"Unsupported printed month header: {header!r}")
    return f"{2000 + int(match[2]):04d}-{_MONTHS[match[1]]:02d}"


def _headers(row: list[str | None]) -> tuple[str, ...]:
    # Empty grid artifacts are not semantic columns. Some current-month header
    # cells span the boundary of an otherwise empty extra grid column.
    cells = [normalize(cell) for cell in row if cell and cell.strip()]
    months = [cell for cell in cells if re.fullmatch(r"[a-z]+-[0-9]{2}", cell)]
    rates = [cell.replace(" ", "") for cell in cells if "mois" in cell]
    if len(months) != 5 or tuple(rates) != _RATE_HEADERS:
        raise ValueError("Expected exactly five source months and 1/3/12-month rate columns")
    periods = tuple(_period(cell) for cell in months)
    ordinals = [int(p[:4]) * 12 + int(p[5:]) for p in periods]
    if (ordinals[0] != ordinals[-1] - 12
            or ordinals[1:] != list(range(ordinals[-1] - 3, ordinals[-1] + 1))):
        raise ValueError("Printed IHPC month sequence does not match the inspected table contract")
    return periods


def _decimal(value: str) -> str:
    compact = re.sub(r"[\s\u00a0\u202f]", "", value)
    if not _NUMBER.fullmatch(compact):
        raise ValueError(f"Invalid printed numeric cell: {value!r}")
    try:
        number = Decimal(compact.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("Invalid printed decimal") from exc
    if not number.is_finite():
        raise ValueError("Non-finite printed decimal")
    return format(number, "f")


def _without_superscript(text: str, chars: list[dict]) -> str:
    """Remove only a geometrically smaller, raised trailing footnote digit.

    In May Table 2, Guinea-Bissau's 103,7 carries superscript 3. Text extraction
    produces 103,73. A real same-baseline second decimal remains part of the value.
    """
    numeric_chars = [c for c in chars if c["text"].strip()]
    if not numeric_chars or not all(c["text"] in "0123456789,.-" for c in numeric_chars):
        return text
    sizes = Counter(round(c["size"], 3) for c in numeric_chars)
    body_size = max(sizes, key=lambda size: (sizes[size], size))
    body = [c for c in numeric_chars if abs(c["size"] - body_size) < 0.01]
    body_bottom = max(c["bottom"] for c in body)
    markers = [c for c in numeric_chars if c["size"] <= body_size * 0.8
               and c["bottom"] < body_bottom - body_size * 0.15]
    if not markers:
        return text
    if (not all(c["text"].isdigit() for c in markers)
            or min(c["x0"] for c in markers) < max(c["x1"] for c in body) - 0.1):
        raise ValueError("Unrecognized mixed-font numeric cell")
    marker_ids = {id(c) for c in markers}
    return "".join(c["text"] for c in sorted(numeric_chars, key=lambda c: c["x0"])
                   if id(c) not in marker_ids)


def _table_grid(page, table) -> list[list[str | None]]:
    grid = table.extract()
    if (not isinstance(grid, list) or len(grid) != len(table.rows)
            or any(not isinstance(row, list) or len(row) != len(physical.cells)
                   for row, physical in zip(grid, table.rows))):
        raise ValueError("Inconsistent PDF table geometry")
    for row, physical in zip(grid, table.rows):
        for column, cell in enumerate(physical.cells):
            text = row[column]
            if cell and text and re.fullmatch(r"-?[0-9]+(?:[,.][0-9]*)?", text.strip()):
                x0, top, x1, bottom = cell
                chars = [c for c in page.chars if x0 <= (c["x0"] + c["x1"]) / 2 < x1
                         and top <= (c["top"] + c["bottom"]) / 2 < bottom]
                row[column] = _without_superscript(text, chars)
    return grid


def _logical_rows(rows: list[list[str | None]], label_column: int) -> list[list[str | None]]:
    """Combine inspected split rows only when their occupied columns do not collide."""
    result = []
    for row in rows:
        if not any(cell and cell.strip() for cell in row):
            continue
        if row[label_column] and row[label_column].strip():
            result.append(list(row))
            continue
        if not result or len(row) != len(result[-1]):
            raise ValueError("Orphan PDF continuation row")
        previous = result[-1]
        for column, value in enumerate(row):
            if value and value.strip():
                if previous[column] and previous[column].strip():
                    raise ValueError("Conflicting cells in PDF continuation row")
                previous[column] = value
    return result


def _values(row: list[str | None], label_column: int) -> list[str]:
    cells = [cell.strip() for cell in row[label_column + 1:] if cell and cell.strip()]
    joined = []
    index = 0
    while index < len(cells):
        cell = cells[index]
        # June's UEMOA February index crosses an extra grid boundary as 102, | 9.
        # Joining is permitted only at a dangling decimal separator, never between
        # two valid integers, which could silently hide a missing observation.
        if re.fullmatch(r"-?[0-9]+,", cell):
            if index + 1 >= len(cells) or not re.fullmatch(r"[0-9]+", cells[index + 1]):
                raise ValueError("Unresolved split decimal cell")
            cell += cells[index + 1]
            index += 1
        joined.append(cell)
        index += 1
    if len(joined) != 9:
        raise ValueError("Expected one excluded weight, five indices and three rate cells")
    # Even the excluded weight must be a valid printed numeric cell; it is never
    # emitted or interpreted as an observed price value by this parser.
    return [_decimal(cell) for cell in joined]


def _fact(doc: str, digest: str, spec: _TableSpec, label: str, geography: str,
          category: str, level: str, concept: str, period: str, value: str) -> dict:
    index = concept == "cpi_index"
    column = f"indice {period}" if index else dict(zip(_RATE_CONCEPTS, _RATE_HEADERS))[concept]
    return dict(
        fact_id=f"{doc}:p{spec.page}:t{spec.table_number}:{concept}:{geography}:{category}:{period}",
        source_doc=doc, content_sha256=digest, source_page=spec.page, concept=concept,
        geography=geography, unit="index" if index else "percent", frequency="monthly",
        category=category, sector="all_sectors", aggregation_level=level,
        price_basis="index_2023" if index else "not_applicable",
        seasonal_adjustment="not_reported", source_variant="waemu:ihpc_2023",
        period=period, value=value,
        locator=f"Tableau {spec.table_number} / {label} / {column} (physical page {spec.page})",
        method="deterministic_table",
    )


def _parse_grid(doc: str, digest: str, spec: _TableSpec, grid: list[list[str | None]]) -> list[dict]:
    if (not isinstance(grid, list) or len(grid) != spec.row_count
            or any(not isinstance(row, list) or len(row) not in {10, 11}
                   or any(cell is not None and not isinstance(cell, str) for cell in row)
                   for row in grid)):
        raise ValueError(f"Unsupported table geometry on physical page {spec.page}")
    periods = _headers(grid[spec.month_row])
    result = []
    for row in _logical_rows(grid[spec.month_row + 1:], spec.label_column):
        label = " ".join(row[spec.label_column].split())
        values = _values(row, spec.label_column)
        alias = normalize(label).rstrip("*").replace("burkina-faso", "burkina faso")
        if spec.countries:
            if alias not in GEOGRAPHIES:
                raise ValueError(f"Unsupported country in inspected regional table: {label}")
            geography, _ = GEOGRAPHIES[alias]
            category, level = "all_items", "total"
        else:
            if alias not in CATEGORY_ALIASES:
                continue  # Not admitted to the reviewed catalog; no invented identity.
            geography = "TG"
            category, level = CATEGORY_ALIASES[alias]
        result.extend(_fact(doc, digest, spec, label, geography, category, level,
                            "cpi_index", period, value)
                      for period, value in zip(periods, values[1:6]))
        result.extend(_fact(doc, digest, spec, label, geography, category, level,
                            concept, periods[-1], value)
                      for concept, value in zip(_RATE_CONCEPTS, values[6:]))
    if not result:
        raise ValueError(f"No supported facts in physical page {spec.page} table {spec.table_number}")
    return result


def read_source_facts(path: Path, source_doc: str, expected_sha256: str) -> list[dict]:
    """Return exact supported source cells from pinned bytes, without DB/API writes.

    Periods come exclusively from the actual table headers. Parsing a verified
    source does not establish its rights, publication date, revision or approval.
    """
    digest = _DIGESTS.get(source_doc)
    profile = PROFILES.get(source_doc, {})
    if (digest is None or expected_sha256 != digest or profile.get("sha256") != digest
            or profile.get("basis") != "2023" or profile.get("variant") != "waemu:ihpc_2023"):
        raise ValueError("Unsupported or changed checksum-bound IHPC source profile")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("IHPC source bytes do not match the inspected checksum")
    facts = []
    with pdfplumber.open(BytesIO(content)) as pdf:
        if len(pdf.pages) != profile["pages"]:
            raise ValueError("Unexpected IHPC physical page count")
        if "base 100 en 2023" not in normalize(pdf.pages[0].extract_text() or ""):
            raise ValueError("Printed index base does not match the reviewed identity")
        for spec in _specs(source_doc):
            page = pdf.pages[spec.page - 1]
            tables = page.find_tables()
            if len(tables) <= spec.table_index:
                raise ValueError(f"Missing inspected table on physical page {spec.page}")
            try:
                grid = _table_grid(page, tables[spec.table_index])
                facts.extend(_parse_grid(source_doc, digest, spec, grid))
            except (AttributeError, IndexError, KeyError, TypeError) as exc:
                # Dependency/layout failures must become a bounded review failure,
                # not a traceback or a leak of arbitrary PDF cell contents.
                raise ValueError(f"Unparseable inspected IHPC table on physical page {spec.page}") from exc
    identifiers = [fact["fact_id"] for fact in facts]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate source cell identity in inspected table facts")
    return facts
