"""Invented data for the local S0.5.3 sandbox, never official statistics.

This module has no I/O, environment or third-party dependency. The corresponding
PDF fixtures print these exact rows; changes require regenerating those sources.
"""

from datetime import date
from decimal import Decimal

FIXTURE_TODAY = date(2026, 10, 15)
SOURCE_VARIANT = "fixture:monthly_inflation"
ROWS_PER_PAGE = 40
CONCEPTS = (
    ("inflation_rate_yoy", "yoy", "Inflation annuelle", "Year-on-year inflation"),
    ("inflation_rate_mom", "mom", "Variation mensuelle des prix", "Monthly price change"),
    ("inflation_rate_3m", "three_month", "Variation des prix sur trois mois", "Three-month price change"),
    ("inflation_rate_12m_average", "average_12m", "Inflation moyenne sur douze mois", "Twelve-month average inflation"),
    ("cpi_index", "cpi_2023", "Indice des prix base 2023", "Consumer price index base 2023"),
)
ASSETS = ("monthly_2023", "monthly_2014", "revision")


def series_definitions() -> list[dict]:
    result = []
    for geography in ("TG", "BJ"):
        for concept, suffix, label_fr, label_en in CONCEPTS:
            result.append({"id": len(result) + 1, "series_key": f"fixture.{geography}.{suffix}",
                           "concept": concept, "geography": geography,
                           "price_basis": "index_2023" if concept == "cpi_index" else "not_applicable",
                           "unit": "index" if concept == "cpi_index" else "percent",
                           "label_fr": label_fr + " (synthétique)",
                           "label_en": label_en + " (synthetic)"})
    result.append({"id": 11, "series_key": "fixture.TG.cpi_2014", "concept": "cpi_index",
                   "geography": "TG", "price_basis": "index_2014", "unit": "index",
                   "label_fr": "Indice des prix base 2014 (synthétique)",
                   "label_en": "Consumer price index base 2014 (synthetic)"})
    return result


def observations() -> list[dict]:
    result = []
    for series in series_definitions():
        geography = 0 if series["geography"] == "TG" else 1
        for offset in range(46):
            period = f"{2023 + offset // 12:04d}-{offset % 12 + 1:02d}"
            concept = series["concept"]
            if concept == "cpi_index":
                value = (Decimal("125") + Decimal(offset) / 5
                         if series["price_basis"] == "index_2014" else
                         Decimal("100") + Decimal(offset) / 10 + geography)
            elif concept == "inflation_rate_yoy":
                value = Decimal("1") + Decimal((offset * 7 + geography * 3) % 29) / 10
            elif concept == "inflation_rate_mom":
                value = Decimal((offset * 3 + geography) % 13 - 6) / 10
            elif concept == "inflation_rate_3m":
                value = Decimal((offset * 5 + geography) % 19 - 5) / 10
            else:
                value = Decimal("1.5") + Decimal((offset + geography) % 17) / 10
            asset = "monthly_2014" if series["price_basis"] == "index_2014" else "monthly_2023"
            result.append({"series_id": series["id"], "series_key": series["series_key"],
                           "period": period, "value": value, "unit": series["unit"],
                           "asset": asset, "revision_status": "final"})
    revisions = [dict(row, asset="revision", revision_status="revised", value=row["value"] + Decimal("0.1"))
                 for row in result if row["series_id"] == 1 and row["period"] >= "2026-06"]
    result.extend(revisions)
    for asset in ASSETS:
        for position, row in enumerate(row for row in result if row["asset"] == asset):
            row.update(source_page=position // ROWS_PER_PAGE + 1,
                       source_locator=f"Synthetic table row {position + 1}")
    return [dict(row, id=position) for position, row in enumerate(result, 1)]


def printed_row(row: dict) -> str:
    return f"{row['series_key']} | {row['period']} | {row['value']} | {row['unit']} | {row['revision_status']}"
