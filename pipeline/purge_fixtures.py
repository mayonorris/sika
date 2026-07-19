"""Remove synthetic fixture rows from the database. Run before any demo or deploy."""
import os
import sqlite3

from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("SIKA_DB", "data/processed/sika.db")


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    obs = con.execute(
        "DELETE FROM observations WHERE source_doc LIKE 'FIXTURE%'"
    ).rowcount
    pas = con.execute("DELETE FROM passages WHERE source_doc LIKE 'FIXTURE%'").rowcount
    con.commit()
    remaining = con.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    docs = con.execute("SELECT COUNT(DISTINCT source_doc) FROM observations").fetchone()[0]
    con.close()
    print(f"Purged {obs} fixture observations and {pas} fixture passages.")
    print(f"Database now holds {remaining} real observations from {docs} documents.")


if __name__ == "__main__":
    main()
