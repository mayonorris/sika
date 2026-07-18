"""Download the official source publications into data/raw/ with clean names.

Usage:
    python pipeline/download_sources.py

Sources located on 2026-07-18 from inseed.tg and bceao.int (public official
publications). INSEED uses a download manager (/download/<id>/) that redirects
to the file; we follow redirects and save under a canonical name.
"""
import urllib.request
from pathlib import Path

RAW = Path("data/raw")

SOURCES = {
    # --- INSEED Togo ---
    "inseed_bulletin_mensuel_2025-09.pdf": "https://inseed.tg/download/7822/",
    "inseed_bulletin_mensuel_2025-08.pdf": "https://inseed.tg/download/7819/",
    "inseed_bulletin_mensuel_2025-07.pdf": "https://inseed.tg/download/7816/",
    "inseed_communique_inflation_2026-06.pdf": "https://inseed.tg/download/7876/",
    "inseed_ihpc_2026-06.pdf": "https://inseed.tg/download/7866/",
    "inseed_ihpc_2026-05.pdf": "https://inseed.tg/download/7798/",
    "inseed_ihpc_2026-04.pdf": "https://inseed.tg/download/7729/",
    "inseed_ipi_mensuel_2015-2026.pdf": "https://inseed.tg/download/7924/",
    "inseed_ipi_trimestriel_2025-T4.pdf": "https://inseed.tg/download/7551/",
    "inseed_pib_estimations_2025.pdf": "https://inseed.tg/download/7746/",
    "inseed_comptes_trimestriels_2025-T4.pdf": "https://inseed.tg/download/7750/",
    "inseed_ica_services_2026-T1.pdf": "https://inseed.tg/download/7894/",
    # --- BCEAO ---
    "bceao_politique_monetaire_2023-06.pdf": (
        "https://www.bceao.int/sites/default/files/2023-07/"
        "Rapport%20sur%20la%20politique%20mone%CC%81taire%20-%20Juin%202023.pdf"
    ),
}

HEADERS = {"User-Agent": "Mozilla/5.0 (sika research pipeline; contact: kadanganorris@gmail.com)"}


def download(name: str, url: str) -> bool:
    dest = RAW / name
    if dest.exists() and dest.stat().st_size > 10_000:
        print(f"  skip (exists) {name}")
        return True
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        if not data.startswith(b"%PDF"):
            print(f"  WARNING: {name} is not a PDF (got {data[:20]!r}), skipped")
            return False
        dest.write_bytes(data)
        print(f"  ok {name} ({len(data)//1024} KB)")
        return True
    except Exception as exc:  # noqa: BLE001 - report and continue
        print(f"  FAILED {name}: {exc}")
        return False


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    ok = sum(download(n, u) for n, u in SOURCES.items())
    print(f"\n{ok}/{len(SOURCES)} documents in {RAW}/")
    if ok < len(SOURCES):
        print("Some downloads failed; re-run, or fetch manually from inseed.tg / bceao.int.")


if __name__ == "__main__":
    main()
