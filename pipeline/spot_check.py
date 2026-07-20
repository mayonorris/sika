"""Draw a random sample of observations for manual verification against source PDFs.

Usage:
    python pipeline/spot_check.py          # 10 random observations
    python pipeline/spot_check.py 15       # custom sample size

Protocol (docs/DATA_SPEC.md): open each source_doc at source_page, verify value,
period and unit. Target 10/10. Any failure: fix the extractor rule, re-run, re-sample.
"""
import os
import sys
import sqlite3

from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("SIKA_DB", "data/processed/sika.db")


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT indicator_label, geography, period, value, unit,
                  source_doc, source_page, confidence
           FROM observations
           WHERE source_doc NOT LIKE 'FIXTURE%'
           ORDER BY RANDOM() LIMIT ?""",
        (n,),
    ).fetchall()
    con.close()
    print(f"Spot-check sample ({len(rows)} observations). Verify each against its source:\n")
    for i, r in enumerate(rows, 1):
        print(f"{i:2}. [{r['source_doc']} p.{r['source_page']}] "
              f"{r['indicator_label']} ({r['geography']}) "
              f"{r['period']} = {r['value']:g} {r['unit']} "
              f"(confiance d'extraction (LLM) : {r['confidence']:.2f})")
    print("\nOpen each PDF/XLSX at the page above and tick: value OK, period OK, unit OK.")


if __name__ == "__main__":
    main()
