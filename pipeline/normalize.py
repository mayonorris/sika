"""Normalize the indicator taxonomy and geography names in place.

The LLM extraction invented indicator codes batch by batch (240 codes for ~1000
rows), splitting single concepts across sibling codes. This script renames raw
codes to the canonical registry of docs/DATA_SPEC.md. It renames ONLY —
values, periods, sources, pages and confidence are never touched. Idempotent.

Usage:
    python pipeline/normalize.py            # apply + report
    python pipeline/normalize.py --dry-run  # report only, no writes
"""
import os
import sys
import sqlite3

from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("SIKA_DB", "data/processed/sika.db")

# Conservative merges only: each mapping unifies codes that name the SAME
# statistical concept. Anything ambiguous stays untouched and is listed for
# manual review by a statistician.
CANONICAL_INDICATORS = {
    # Inflation / prices
    "inflation_rate": "inflation_rate_yoy",
    "monthly_price_change": "inflation_rate_mom",
    "cpi_index": "consumer_price_index",
    "cpi_weight": "cpi_subindex_weight",
    "producer_price_index": "industrial_producer_price",
    "producer_price_index_yoy": "industrial_producer_price_yoy",
    "producer_price_index_mom": "industrial_producer_price_mom",
    # GDP growth
    "gdp_growth_yoy": "gdp_growth",
    "gdp_growth_real": "gdp_growth",
    # GDP levels
    "gdp_current_prices": "gdp_nominal",
    "gdp_constant_prices": "gdp_real",
    # Growth contributions by sector
    "gdp_growth_contribution": "gdp_growth_sector_contribution",
    "vab_contribution_to_gdp_growth": "gdp_growth_sector_contribution",
    "vab_growth_contribution": "gdp_growth_sector_contribution",
    # Value added levels (gva_* and value_added_* name the same aggregates)
    "gva_nominal": "value_added_nominal",
    "gva_real": "value_added_real",
    "value_added_growth_real": "vab_growth_yoy",
    "vab_growth": "vab_growth_yoy",
    "gva_primary_nominal": "primary_sector_value_added_nominal",
    "gva_primary_real": "primary_sector_value_added_real",
    "gva_secondary_nominal": "secondary_sector_value_added_nominal",
    "gva_secondary_real": "secondary_sector_value_added_real",
    "gva_tertiary_nominal": "tertiary_sector_value_added_nominal",
    "gva_tertiary_real": "tertiary_sector_value_added_real",
    "value_added_primary_current": "primary_sector_value_added_nominal",
    "value_added_primary_constant": "primary_sector_value_added_real",
    "value_added_secondary_current": "secondary_sector_value_added_nominal",
    "value_added_secondary_constant": "secondary_sector_value_added_real",
    "value_added_tertiary_current": "tertiary_sector_value_added_nominal",
    "value_added_tertiary_constant": "tertiary_sector_value_added_real",
    # Net taxes
    "net_taxes_on_products_real": "net_taxes_real",
    "net_taxes_on_products_nominal": "net_taxes_nominal",
    # Credit / monetary aggregates
    "bank_credit_to_economy": "credit_to_economy",
    "claims_on_economy_depository_corporations": "credit_to_economy",
    "commercial_bank_claims_on_economy": "credit_to_economy",
    "bceao_claims_on_economy": "credit_to_economy",
    "bank_credit_to_private_sector": "credit_to_private_sector",
    "net_claims_on_central_government_depository_corporations": "net_claims_on_central_government",
    "net_claims_on_central_government_bceao": "net_claims_on_central_government",
    "central_bank_net_claims_on_central_government": "net_claims_on_central_government",
    # External accounts
    "current_account_deficit_gdp_ratio": "current_account_deficit_to_gdp",
}

GEOGRAPHY_MAP = {
    "Burkina-Faso": "Burkina Faso",
    "Côte d’Ivoire": "Côte d'Ivoire",
    "Cote d'Ivoire": "Côte d'Ivoire",
    "Eurozone": "Zone euro",
    "US": "États-Unis",
    "USA": "États-Unis",
    "CN": "Chine",
    "IN": "Inde",
    "ZA": "Afrique du Sud",
    "UK": "Royaume-Uni",
    "GB": "Royaume-Uni",
}


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    con = sqlite3.connect(DB_PATH)

    before = dict(
        con.execute("SELECT indicator, COUNT(*) FROM observations GROUP BY indicator")
    )
    changed = 0
    for raw, canonical in CANONICAL_INDICATORS.items():
        if raw in before:
            n = con.execute(
                "UPDATE observations SET indicator = ? WHERE indicator = ?",
                (canonical, raw),
            ).rowcount
            changed += n
            print(f"indicator  {raw:55} -> {canonical:45} ({n} rows)")

    geo_changed = 0
    for raw, canonical in GEOGRAPHY_MAP.items():
        n = con.execute(
            "UPDATE observations SET geography = ? WHERE geography = ?",
            (canonical, raw),
        ).rowcount
        if n:
            geo_changed += n
            print(f"geography  {raw:55} -> {canonical:45} ({n} rows)")

    if dry_run:
        con.rollback()
        print("\nDRY RUN: no changes written.")
    else:
        con.commit()

    after = dict(
        con.execute("SELECT indicator, COUNT(*) FROM observations GROUP BY indicator")
    )
    total = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    print(
        f"\nIndicators: {len(before)} -> {len(after)} distinct codes. "
        f"Rows renamed: {changed} indicator, {geo_changed} geography. "
        f"Total observations unchanged: {total}."
    )

    mapped = set(CANONICAL_INDICATORS) | set(CANONICAL_INDICATORS.values())
    review = sorted(
        code for code, n in after.items() if code not in mapped and n <= 2
    )
    if review:
        print(f"\nREVIEW ({len(review)} low-count codes left untouched, "
              "merge manually if appropriate):")
        for code in review:
            print(f"  {after[code]}x  {code}")
    con.close()


if __name__ == "__main__":
    main()
