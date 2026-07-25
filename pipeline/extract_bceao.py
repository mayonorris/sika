"""Deterministic ingestion of BCEAO publications: country-by-country tables.

BCEAO publications (bulletin mensuel de statistiques, rapports de politique
monetaire) present the same indicators for the 8 WAEMU members in recurring
table layouts. This parser detects those country matrices with pdfplumber and
ingests them WITHOUT any LLM: values, periods and country names are read
deterministically, so nothing can be invented. Confidence is 1.0.

Only row labels present in BCEAO_SERIES are ingested; everything else is
reported as skipped so the registry can be extended deliberately.

Usage:
    python pipeline/extract_bceao.py data/raw/bceao_politique_monetaire_2023-06.pdf
    python pipeline/extract_bceao.py data/raw/          # every bceao_*.pdf
    python pipeline/extract_bceao.py --dry-run data/raw # parse, print, no insert
    python pipeline/extract_bceao.py --discover         # fetch new bulletins
"""
import os
import re
import sys
import sqlite3
import unicodedata
from pathlib import Path

import pdfplumber
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("SIKA_DB", "data/processed/sika.db")
RAW_DIR = Path("data/raw")

COUNTRIES = {
    "benin": "Bénin",
    "burkina": "Burkina Faso",
    "cote d'ivoire": "Côte d'Ivoire",
    "cote divoire": "Côte d'Ivoire",
    "cote": "Côte d'Ivoire",
    "guinee": "Guinée-Bissau",
    "guinee-bissau": "Guinée-Bissau",
    "guinee bissau": "Guinée-Bissau",
    "mali": "Mali",
    "niger": "Niger",
    "senegal": "Sénégal",
    "togo": "Togo",
    "uemoa": "UEMOA",
    "umoa": "UEMOA",
    "union": "UEMOA",
}

# Normalized row-label prefix -> (canonical indicator, unit).
# Deliberately conservative: extend after inspecting each new table type.
BCEAO_SERIES = {
    "taux d'inflation": ("inflation_rate_yoy", "%"),
    "inflation": ("inflation_rate_yoy", "%"),
    "taux de croissance du pib": ("gdp_growth", "%"),
    "croissance du pib": ("gdp_growth", "%"),
    "taux de croissance": ("gdp_growth", "%"),
    "credits a l'economie": ("credit_to_economy", "milliards FCFA"),
    "credit a l'economie": ("credit_to_economy", "milliards FCFA"),
    "concours a l'economie": ("credit_to_economy", "milliards FCFA"),
    "masse monetaire": ("broad_money", "milliards FCFA"),
    "avoirs exterieurs nets": ("net_foreign_assets", "milliards FCFA"),
    "actifs exterieurs nets": ("net_foreign_assets", "milliards FCFA"),
    "monnaie au sens large": ("broad_money", "milliards FCFA"),
    "taux directeur": ("policy_rate", "%"),
    "taux minimum de soumission": ("policy_rate", "%"),
    "taux du guichet de pret marginal": ("marginal_lending_rate", "%"),
    "taux interbancaire": ("interbank_rate", "%"),
    "deficit budgetaire": ("budget_deficit_to_gdp", "% du PIB"),
    "solde budgetaire": ("budget_balance_to_gdp", "% du PIB"),
    "solde courant": ("current_account_balance_to_gdp", "% du PIB"),
    "encours de la dette": ("public_debt", "milliards FCFA"),
    "taux d'endettement": ("public_debt_to_gdp", "% du PIB"),
    # Bulletin mensuel de statistiques (annexes pays en colonnes)
    "circulation fiduciaire": ("currency_in_circulation", "milliards FCFA"),
    "depots transferables": ("transferable_deposits", "milliards FCFA"),
    "m1": ("money_supply_m1", "milliards FCFA"),
    "m2": ("broad_money", "milliards FCFA"),
    "creances interieures": ("domestic_claims", "milliards FCFA"),
    "creances nettes sur l'administration centrale": (
        "net_claims_on_central_government", "milliards FCFA"),
    "creances nettes sur les apu": (
        "net_claims_on_central_government", "milliards FCFA"),
    "creances sur l'economie": ("credit_to_economy", "milliards FCFA"),
    "creances sur le secteur prive": ("credit_to_private_sector", "milliards FCFA"),
    "base monetaire": ("monetary_base", "milliards FCFA"),
    "indice harmonise": ("consumer_price_index", "index"),
    "indice global": ("consumer_price_index", "index"),
    "variation annuelle": ("inflation_rate_yoy", "%"),
    "variation mensuelle": ("inflation_rate_mom", "%"),
    "variation sur un an": ("inflation_rate_yoy", "%"),
    "variation sur un mois": ("inflation_rate_mom", "%"),
}

QUARTER_WORDS = {
    "premier": "Q1", "deuxieme": "Q2", "troisieme": "Q3", "quatrieme": "Q4",
    "1er": "Q1", "2e": "Q2", "2eme": "Q2", "3e": "Q3", "3eme": "Q3",
    "4e": "Q4", "4eme": "Q4",
}

MONTHS = {
    "janv": "01", "janvier": "01", "fevr": "02", "fev": "02", "fevrier": "02",
    "mars": "03", "avr": "04", "avril": "04", "mai": "05", "juin": "06",
    "juil": "07", "juillet": "07", "aout": "08", "sept": "09", "septembre": "09",
    "oct": "10", "octobre": "10", "nov": "11", "novembre": "11",
    "dec": "12", "decembre": "12",
}

SCHEMA_HINT = (
    "Run pipeline/extract.py once first, or ensure data/processed/sika.db "
    "already has the observations/passages tables."
)


def normalized(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plain.lower()).strip()


def match_country(cell: str) -> str | None:
    clean = normalized(cell)
    if not clean or len(clean) > 30:
        return None
    for key, canonical in COUNTRIES.items():
        if clean == key or clean.startswith(key):
            return canonical
    return None


def match_series(cell: str) -> tuple[str, str] | None:
    clean = normalized(cell)
    for prefix, mapped in BCEAO_SERIES.items():
        if clean.startswith(prefix):
            return mapped
    return None


def parse_period(cell: str) -> str | None:
    clean = normalized(cell).replace(".", "").replace("*", "").strip()
    if re.fullmatch(r"20\d{2}", clean):
        return clean
    quarter = re.fullmatch(r"t\s?([1-4])[\s-]*(20\d{2})", clean)
    if quarter:
        return f"{quarter.group(2)}-Q{quarter.group(1)}"
    quarter2 = re.fullmatch(r"(20\d{2})[\s-]*t\s?([1-4])", clean)
    if quarter2:
        return f"{quarter2.group(1)}-Q{quarter2.group(2)}"
    month_year = re.fullmatch(r"([a-z]{3,9})[\s-]*(20\d{2})", clean)
    if month_year and month_year.group(1) in MONTHS:
        return f"{month_year.group(2)}-{MONTHS[month_year.group(1)]}"
    month_short = re.fullmatch(r"([a-z]{3,9})[\s-]*(\d{2})", clean)
    if month_short and month_short.group(1) in MONTHS:
        return f"20{month_short.group(2)}-{MONTHS[month_short.group(1)]}"
    return None


def parse_value(cell: str) -> float | None:
    clean = (cell or "").strip().replace(" ", " ")
    if not clean or clean in {"-", "--", "n.d.", "nd", "..."}:
        return None
    negative = clean.startswith("(") and clean.endswith(")")
    clean = clean.strip("()").replace(" ", "").replace(",", ".")
    clean = re.sub(r"[^0-9.\-]", "", clean)
    if clean in {"", "-", "."}:
        return None
    try:
        value = float(clean)
    except ValueError:
        return None
    return -value if negative else value


def table_observations(
    table: list[list[str]], page_number: int, source_doc: str
) -> tuple[list[dict], set[str]]:
    """Extract observations from one table, both orientations:
    A) countries as columns, series as rows;
    B) countries as rows, periods as columns (series named in a header cell)."""
    observations: list[dict] = []
    skipped: set[str] = set()
    if not table or not table[0]:
        return observations, skipped

    header = [cell or "" for cell in table[0]]
    header_countries = {
        idx: country
        for idx, cell in enumerate(header)
        if (country := match_country(cell))
    }
    header_periods = {
        idx: period
        for idx, cell in enumerate(header)
        if (period := parse_period(cell))
    }

    # Orientation A: >=4 countries across the header.
    if len(header_countries) >= 4:
        current_period_by_col: dict[int, str] = {}
        for row in table[1:]:
            if not row or not row[0]:
                continue
            row_label = str(row[0]).strip()
            period_in_label = parse_period(row_label)
            series = match_series(row_label)
            if series is None:
                if period_in_label is None and normalized(row_label):
                    skipped.add(row_label[:60])
                continue
            indicator, unit = series
            embedded = re.search(r"(20\d{2})", row_label)
            for idx, country in header_countries.items():
                if idx >= len(row):
                    continue
                value = parse_value(str(row[idx] or ""))
                if value is None:
                    continue
                period = (
                    period_in_label
                    or current_period_by_col.get(idx)
                    or (embedded.group(1) if embedded else None)
                )
                if period is None:
                    continue
                observations.append({
                    "indicator": indicator,
                    "indicator_label": row_label,
                    "geography": country,
                    "period": period,
                    "value": value,
                    "unit": unit,
                    "source_doc": source_doc,
                    "source_page": page_number,
                })
        return observations, skipped

    # Orientation B: countries down the first column, periods across the header.
    first_col_countries = [
        (r, country)
        for r, row in enumerate(table)
        if row and row[0] and (country := match_country(str(row[0])))
    ]
    if len(first_col_countries) >= 4 and header_periods:
        series = None
        for cell in header:
            if (candidate := match_series(str(cell))) is not None:
                series = candidate
                break
        if series is None:
            for row in table:
                for cell in row or []:
                    if (candidate := match_series(str(cell or ""))) is not None:
                        series = candidate
                        break
                if series:
                    break
        if series is None:
            skipped.add(f"[p.{page_number}] tableau pays sans série reconnue")
            return observations, skipped
        indicator, unit = series
        for r, country in first_col_countries:
            row = table[r]
            for idx, period in header_periods.items():
                if idx >= len(row):
                    continue
                value = parse_value(str(row[idx] or ""))
                if value is None:
                    continue
                observations.append({
                    "indicator": indicator,
                    "indicator_label": f"{indicator} ({country})",
                    "geography": country,
                    "period": period,
                    "value": value,
                    "unit": unit,
                    "source_doc": source_doc,
                    "source_page": page_number,
                })
    return observations, skipped


def title_period(line_text: str) -> str | None:
    """Period from a table title: 'à fin janvier 2026', 'au premier trimestre
    2026', or a bare 'janvier 2026'."""
    clean = normalized(line_text)
    if "tableau" not in clean and "fin" not in clean:
        return None
    months = "|".join(sorted(MONTHS, key=len, reverse=True))
    fin = re.search(rf"fin\s+({months})[a-z]*\s+(20\d{{2}})", clean)
    if fin:
        return f"{fin.group(2)}-{MONTHS[fin.group(1)]}"
    quarter = re.search(
        rf"({'|'.join(QUARTER_WORDS)})\s+trimestre\s+(20\d{{2}})", clean
    )
    if quarter:
        return f"{quarter.group(2)}-{QUARTER_WORDS[quarter.group(1)]}"
    bare = re.search(rf"\b({months})[a-z]*\s+(20\d{{2}})\b", clean)
    if bare:
        return f"{bare.group(2)}-{MONTHS[bare.group(1)]}"
    return None


def group_lines(words: list[dict]) -> list[list[dict]]:
    lines: dict[int, list[dict]] = {}
    for word in words:
        lines.setdefault(round(word["top"] / 3), []).append(word)
    ordered = [sorted(ws, key=lambda w: w["x0"]) for _, ws in sorted(lines.items())]
    return ordered


def line_country_columns(line: list[dict]) -> list[tuple[float, str]]:
    """Column anchors (x0, country) when a line names >=4 UEMOA members."""
    columns: list[tuple[float, str]] = []
    for word in line:
        country = match_country(word["text"])
        if country and all(existing != country for _, existing in columns):
            columns.append((word["x0"], country))
    return columns if len(columns) >= 4 else []


def words_observations(page, source_doc: str) -> tuple[list[dict], set[str]]:
    """Bulletin layout: series down the side, countries across the top,
    the period carried by the table title. Rebuilt from word positions
    because BCEAO tables have no vertical rules in the body."""
    observations: list[dict] = []
    skipped: set[str] = set()
    lines = group_lines(page.extract_words() or [])
    period: str | None = None
    columns: list[tuple[float, str]] = []
    bounds: list[float] = []
    for line in lines:
        text = " ".join(word["text"] for word in line)
        found_period = title_period(text)
        if found_period:
            period = found_period
            continue
        anchors = line_country_columns(line)
        if anchors:
            columns = anchors
            xs = [x for x, _ in columns]
            bounds = [xs[0] - 25] + [
                (xs[i] + xs[i + 1]) / 2 for i in range(len(xs) - 1)
            ] + [xs[-1] + 60]
            continue
        if not columns or period is None:
            continue
        label_words = [w for w in line if w["x1"] < bounds[0]]
        value_words = [w for w in line if w["x1"] >= bounds[0]]
        if len(value_words) < 4 or not label_words:
            continue
        label = " ".join(w["text"] for w in label_words)
        series = match_series(label)
        if series is None:
            skipped.add(label[:60])
            continue
        indicator, unit = series
        per_column: dict[int, list[str]] = {}
        for word in value_words:
            center = (word["x0"] + word["x1"]) / 2
            for idx in range(len(columns)):
                if bounds[idx] <= center < bounds[idx + 1]:
                    per_column.setdefault(idx, []).append(word["text"])
                    break
        for idx, fragments in per_column.items():
            value = parse_value(" ".join(fragments))
            if value is None:
                continue
            observations.append({
                "indicator": indicator,
                "indicator_label": label,
                "geography": columns[idx][1],
                "period": period,
                "value": value,
                "unit": unit,
                "source_doc": source_doc,
                "source_page": page.page_number,
            })
    return observations, skipped


def process_pdf(pdf_path: Path, con: sqlite3.Connection, dry_run: bool) -> int:
    source_doc = pdf_path.name
    inserted = 0
    all_skipped: set[str] = set()
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            observations, skipped = words_observations(page, source_doc)
            all_skipped |= skipped
            for ob in observations:
                    exists = con.execute(
                        """SELECT 1 FROM observations
                           WHERE indicator=? AND geography=? AND period=?
                             AND source_doc=? AND source_page=?""",
                        (ob["indicator"], ob["geography"], ob["period"],
                         ob["source_doc"], ob["source_page"]),
                    ).fetchone()
                    if exists:
                        continue
                    if dry_run:
                        print(f"  [p.{ob['source_page']:>3}] {ob['indicator']:28} "
                              f"{ob['geography']:14} {ob['period']:8} = "
                              f"{ob['value']:>12g} {ob['unit']}")
                    else:
                        con.execute(
                            """INSERT INTO observations
                               (indicator, indicator_label, geography, period,
                                value, unit, source_doc, source_page, confidence)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1.0)""",
                            (ob["indicator"], ob["indicator_label"],
                             ob["geography"], ob["period"], ob["value"],
                             ob["unit"], ob["source_doc"], ob["source_page"]),
                        )
                    inserted += 1
    if not dry_run:
        con.commit()
    print(f"{source_doc}: {inserted} observation(s) "
          f"{'trouvées (dry-run)' if dry_run else 'insérées'}.")
    if all_skipped:
        print(f"  Libellés non reconnus (à ajouter au registre si pertinents) :")
        for label in sorted(all_skipped)[:15]:
            print(f"    - {label}")
    return inserted


def discover() -> list[Path]:
    """Fetch the BCEAO statistics listing and download new bulletin PDFs."""
    import requests

    listing_urls = (
        "https://www.bceao.int/fr/publications/bulletin-mensuel-des-statistiques",
        "https://www.bceao.int/fr/publications",
    )
    found: list[Path] = []
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for url in listing_urls:
        try:
            html = requests.get(url, timeout=30).text
        except requests.RequestException as error:
            print(f"discover: échec sur {url} ({error})")
            continue
        links = set(re.findall(r'href="([^"]+\.pdf)"', html))
        for link in links:
            if not re.search(r"bulletin|statisti", link, re.IGNORECASE):
                continue
            full = link if link.startswith("http") else f"https://www.bceao.int{link}"
            name = "bceao_" + re.sub(r"[^a-z0-9.-]+", "_", full.rsplit("/", 1)[-1].lower())
            target = RAW_DIR / name
            if target.exists():
                continue
            try:
                data = requests.get(full, timeout=60).content
            except requests.RequestException:
                continue
            if not data.startswith(b"%PDF"):
                continue
            target.write_bytes(data)
            print(f"discover: téléchargé {target.name} ({len(data) // 1024} KiB)")
            found.append(target)
    if not found:
        print("discover: aucun nouveau bulletin (listing en JavaScript ? "
              "déposez le PDF manuellement dans data/raw/).")
    return found


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("SELECT 1 FROM observations LIMIT 1")
    except sqlite3.Error:
        sys.exit(f"Base indisponible ({DB_PATH}). {SCHEMA_HINT}")

    targets: list[Path] = []
    if "--discover" in sys.argv:
        targets.extend(discover())
    for arg in args:
        path = Path(arg)
        if path.is_dir():
            targets.extend(sorted(path.glob("bceao_*.pdf")))
        elif path.exists():
            targets.append(path)
    if not targets:
        sys.exit("Aucun PDF BCEAO à traiter. Usage en tête de fichier.")

    total = 0
    for pdf_path in targets:
        total += process_pdf(pdf_path, con, dry_run)
    con.close()
    print(f"\nTotal : {total} observation(s) sur {len(targets)} document(s).")


if __name__ == "__main__":
    main()
