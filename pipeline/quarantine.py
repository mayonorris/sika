"""Demote observations that failed manual verification against their source.

Rows listed here are NOT deleted: their confidence is set below the serving
threshold (0.5), so they vanish from answers while staying auditable in the
database. Reversible by re-running extraction or restoring confidence.

Each entry documents WHO failed verification and WHY. Idempotent.
"""
import os
import sqlite3

from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("SIKA_DB", "data/processed/sika.db")
DEMOTED_CONFIDENCE = 0.3

# (source_doc, indicator_label LIKE pattern, reason)
QUARANTINE = [
    (
        "inseed_bulletin_mensuel_2025-09.pdf",
        "CREANCES DES INSTITUTIONS%",
        "2026-07-25 revue manuelle: valeurs non retrouvées p.25, unité suspecte",
    ),
    (
        "inseed_bulletin_mensuel_2025-09.pdf",
        "Créances de la BCEAO%",
        "2026-07-25 revue manuelle: valeurs non retrouvées p.25, unité suspecte",
    ),
    (
        "inseed_bulletin_mensuel_2025-09.pdf",
        "Créances des banques%",
        "2026-07-25 revue manuelle: valeurs non retrouvées p.25, unité suspecte",
    ),
    (
        "inseed_bulletin_mensuel_2025-09.pdf",
        "Crédit bancaire%",
        "2026-07-25 revue manuelle: valeurs non retrouvées p.25, unité suspecte",
    ),
]


def main() -> None:
    con = sqlite3.connect(DB_PATH)
    total = 0
    for source_doc, pattern, reason in QUARANTINE:
        n = con.execute(
            """UPDATE observations SET confidence = ?
               WHERE source_doc = ? AND indicator_label LIKE ?
                 AND confidence > ?""",
            (DEMOTED_CONFIDENCE, source_doc, pattern, DEMOTED_CONFIDENCE),
        ).rowcount
        total += n
        if n:
            print(f"demoted {n:3} | {source_doc} | {pattern} | {reason}")
    con.commit()
    remaining = con.execute(
        "SELECT COUNT(*) FROM observations WHERE confidence >= 0.5"
    ).fetchone()[0]
    print(f"\n{total} observation(s) mises en quarantaine. "
          f"{remaining} observations servies (confiance >= 0.5).")
    con.close()


if __name__ == "__main__":
    main()
