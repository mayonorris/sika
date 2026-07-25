"""Sika API: cited answers and generated briefs over official statistics."""
import json
import os
import re
import sqlite3
import unicodedata

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import APIError, APITimeoutError, OpenAI
from pydantic import BaseModel, Field

load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip()
client = (
    OpenAI(api_key=API_KEY, base_url=BASE_URL or None)
    if API_KEY and "your-" not in API_KEY
    else None
)
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6")
DB_PATH = os.getenv("SIKA_DB", "data/processed/sika.db")

app = FastAPI(title="Sika", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(APITimeoutError)
@app.exception_handler(APIError)
async def openai_failure(_request: Request, _error: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "Le service d'analyse est temporairement indisponible. Réessayez dans un instant."},
    )


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


class Ask(BaseModel):
    question: str = Field(min_length=2, max_length=500)


SOURCE_METADATA = {
    "inseed_ipi_mensuel_2015-2026.xlsx": {
        "publisher": "INSEED Togo",
        "title": "Indice de la production industrielle mensuel rénové, 2015–2026",
        "url": "https://inseed.tg/download/7924/",
    },
    "inseed_ipi_trimestriel_2025-T4.xlsx": {
        "publisher": "INSEED Togo",
        "title": "Indice des prix de production de l'industrie, T4 2025",
        "url": "https://inseed.tg/download/7551/",
    },
    "inseed_ica_services_2026-T1.xlsx": {
        "publisher": "INSEED Togo",
        "title": "Indice du chiffre d'affaires dans les services, T1 2026",
        "url": "https://inseed.tg/download/7894/",
    },
    "inseed_bulletin_mensuel_2025-09.pdf": {
        "publisher": "INSEED Togo",
        "title": "Bulletin mensuel des statistiques, septembre 2025",
        "url": "https://inseed.tg/download/7822/",
    },
    "inseed_ihpc_2026-06.pdf": {
        "publisher": "INSEED Togo",
        "title": "Indice harmonisé des prix à la consommation, juin 2026",
        "url": "https://inseed.tg/download/7866/",
    },
    "inseed_ihpc_2026-05.pdf": {
        "publisher": "INSEED Togo",
        "title": "Indice harmonisé des prix à la consommation, mai 2026",
        "url": "https://inseed.tg/download/7798/",
    },
    "inseed_pib_estimations_2025.pdf": {
        "publisher": "INSEED Togo",
        "title": "Premières estimations du PIB, 2025",
        "url": "https://inseed.tg/download/7746/",
    },
    "inseed_comptes_trimestriels_2025-T4.pdf": {
        "publisher": "INSEED Togo",
        "title": "Comptes nationaux trimestriels, T4 2025",
        "url": "https://inseed.tg/download/7750/",
    },
    "bceao_politique_monetaire_2023-06.pdf": {
        "publisher": "BCEAO",
        "title": "Rapport sur la politique monétaire dans l'UEMOA, juin 2023",
        "url": "https://www.bceao.int/fr/publications/rapport-sur-la-politique-monetaire-dans-luemoa",
    },
}


INDICATOR_RULES = (
    (("inflation", "ihpc", "prix"), ("inflation_rate_yoy",)),
    (
        ("chiffre d'affaires", "chiffre affaires", "turnover", "ica"),
        ("turnover_index_services", "turnover_index_industry"),
    ),
    (
        ("production industrielle", "industrial production", "ipi"),
        ("industrial_production_index",),
    ),
    (
        ("microfinance", "depot", "credit"),
        ("credit_to_economy", "credit_to_private_sector", "deposits_microfinance"),
    ),
    (("taux directeur", "policy rate"), ("policy_rate",)),
    (("pib", "gdp", "croissance"), ("gdp_growth", "gdp_nominal")),
)


HEADLINE_HINTS = (
    "global",
    "ensemble",
    "ihpc au togo",
    "depuis 12 mois",
    "taux d'inflation",
)
CATEGORY_HINTS = {
    "alimentation": ("alimentation", "alimentaire", "aliment"),
    "transport": ("transport",),
    "sante": ("sante",),
    "logement": ("logement",),
    "energie": ("energie", "electricite", "combustible"),
}
MONTH_NUMBERS = {
    "janvier": "01",
    "fevrier": "02",
    "mars": "03",
    "avril": "04",
    "mai": "05",
    "juin": "06",
    "juillet": "07",
    "aout": "08",
    "septembre": "09",
    "octobre": "10",
    "novembre": "11",
    "decembre": "12",
}
GEOGRAPHY_HINTS = (
    "togo", "uemoa", "benin", "burkina", "ivoire",
    "bissau", "mali", "niger", "senegal",
)
GEOGRAPHY_RULES = (
    (r"\buemoa\b|\bunion (?:economique et )?monetaire\b", "UEMOA"),
    (r"\bbenin\b|\bbeninois", "Bénin"),
    (r"\bburkina\b", "Burkina Faso"),
    (r"\bivoire\b|\bivoirien", "Côte d'Ivoire"),
    (r"\bbissau\b|\bguinee\b", "Guinée-Bissau"),
    (r"\bmali\b|\bmalien", "Mali"),
    (r"\bniger\b|\bnigerien", "Niger"),
    (r"\bsenegal\b", "Sénégal"),
    (r"\btogo\b|\btogolais", "Togo"),
)


def normalized(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plain.lower()).strip()


def fallback_indicators(question: str) -> tuple[str, ...]:
    clean = normalized(question)
    for terms, indicators in INDICATOR_RULES:
        if any(re.search(rf"\b{re.escape(term)}", clean) for term in terms):
            return indicators
    return ()


def names_different_geography(row: dict) -> bool:
    label = normalized(row["indicator_label"])
    geography = normalized(row["geography"])
    named = [name for name in GEOGRAPHY_HINTS if name in label]
    return bool(named) and not any(name in geography for name in named)


def pick_headline_rows(rows: list[dict]) -> list[dict]:
    candidates = [
        row
        for row in rows
        if not names_different_geography(row)
        and any(hint in normalized(row["indicator_label"]) for hint in HEADLINE_HINTS)
    ]
    return candidates or rows


def row_tie_key(row: dict) -> tuple[str, str, str, str]:
    return (
        normalized(cleaned_row_label(row)),
        str(row.get("source_doc", "")),
        str(row.get("source_page", "")),
        str(row.get("value", "")),
    )


def dedupe_by_period(rows: list[dict]) -> list[dict]:
    preferred_label = canonical_label(rows)
    best: dict[str, dict] = {}
    for row in rows:
        current = best.get(row["period"])
        row_confidence = row.get("confidence", 0)
        current_confidence = current.get("confidence", 0) if current else -1
        row_matches = cleaned_row_label(row) == preferred_label
        current_matches = current is not None and cleaned_row_label(current) == preferred_label

        if (
            current is None
            or row_confidence > current_confidence
            or (row_confidence == current_confidence and row_matches and not current_matches)
            or (
                row_confidence == current_confidence
                and row_matches == current_matches
                and row_tie_key(row) < row_tie_key(current)
            )
        ):
            best[row["period"]] = row
    return [best[period] for period in sorted(best)]


def requested_category(question: str) -> tuple[str, ...]:
    clean = normalized(question)
    for hints in CATEGORY_HINTS.values():
        if any(hint in clean for hint in hints):
            return hints
    return ()


def filter_requested_rows(question: str, rows: list[dict]) -> list[dict]:
    category = requested_category(question)
    if category:
        rows = [
            row
            for row in rows
            if any(hint in normalized(row["indicator_label"]) for hint in category)
        ]
    else:
        rows = pick_headline_rows(rows)
    return dedupe_by_period(rows)


def parse_geography(question: str) -> str | None:
    clean = normalized(question)
    for pattern, canonical in GEOGRAPHY_RULES:
        if re.search(pattern, clean):
            return canonical
    return None


def parse_period(question: str) -> tuple[str, ...] | None:
    clean = normalized(question)
    iso = re.search(r"\b(20\d{2}-(?:0[1-9]|1[0-2]))\b", clean)
    if iso:
        return ("exact", iso.group(1))
    months = "|".join(MONTH_NUMBERS)
    since_month = re.search(rf"\b(?:depuis|since)\s+({months})\s+(20\d{{2}})\b", clean)
    if since_month:
        return ("since", f"{since_month.group(2)}-{MONTH_NUMBERS[since_month.group(1)]}")
    month_span = re.search(
        rf"\b(?:entre|de|from)\s+({months})\s+(20\d{{2}})\s+(?:et|a|to)\s+({months})\s+(20\d{{2}})\b",
        clean,
    )
    if month_span:
        bounds = sorted((
            f"{month_span.group(2)}-{MONTH_NUMBERS[month_span.group(1)]}",
            f"{month_span.group(4)}-{MONTH_NUMBERS[month_span.group(3)]}",
        ))
        return ("range", bounds[0], bounds[1])
    named = re.search(rf"\b({months})\s+(20\d{{2}})\b", clean)
    if named:
        return ("exact", f"{named.group(2)}-{MONTH_NUMBERS[named.group(1)]}")
    span = re.search(
        r"\b(?:entre|de|from)\s+(20\d{2})\s+(?:et|a|to)\s+(20\d{2})\b", clean
    ) or re.search(r"\b(20\d{2})\s*(?:-|a|au)\s*(20\d{2})\b", clean)
    if span:
        start, end = sorted((span.group(1), span.group(2)))
        return ("range", start, end)
    since = re.search(r"\b(?:depuis|since)\s+(20\d{2})\b", clean)
    if since:
        return ("since", since.group(1))
    last = (
        re.search(r"\b(\d{1,2})\s+derniers?\s+mois\b", clean)
        or re.search(r"\bderniers?\s+(\d{1,2})\s+mois\b", clean)
        or re.search(r"\blast\s+(\d{1,2})\s+months\b", clean)
    )
    if last:
        return ("last", last.group(1))
    year = re.search(r"\b(?:en|in)\s+(20\d{2})\b", clean)
    if year:
        return ("year", year.group(1))
    bare = re.search(r"\b(20\d{2})\b", clean)
    if bare:
        return ("year", bare.group(1))
    return None


def query_indicator_rows(
    con: sqlite3.Connection,
    indicator: str,
    geography: str,
    period_filter: tuple[str, ...] | None,
) -> list[dict]:
    real = con.execute(
        """SELECT COUNT(*) FROM observations
           WHERE indicator = ? AND source_doc NOT LIKE 'FIXTURE%'""",
        (indicator,),
    ).fetchone()[0]
    source_clause = " AND source_doc NOT LIKE 'FIXTURE%'" if real else ""
    params: list[object] = [indicator, geography]
    period_clause = ""
    if period_filter:
        mode = period_filter[0]
        if mode == "exact":
            period_clause = " AND period = ?"
            params.append(period_filter[1])
        elif mode == "year":
            period_clause = " AND period LIKE ?"
            params.append(f"{period_filter[1]}%")
        elif mode == "since":
            period_clause = " AND period >= ?"
            params.append(period_filter[1])
        elif mode == "range":
            period_clause = " AND period >= ? AND period <= ?"
            params.extend([period_filter[1], period_filter[2] + "~"])
    rows = con.execute(
        f"""SELECT indicator, indicator_label, geography, period, value, unit,
                   source_doc, source_page, confidence
            FROM observations WHERE indicator = ? AND geography = ?
              AND confidence >= 0.5{source_clause}{period_clause}
            ORDER BY period LIMIT 200""",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def select_fallback_rows(con: sqlite3.Connection, question: str) -> list[dict]:
    candidates = fallback_indicators(question)
    if not candidates:
        return []
    geography = parse_geography(question) or "Togo"
    period_filter = parse_period(question)
    result: list[dict] = []
    best_quality = -1.0
    for indicator in candidates:
        rows = query_indicator_rows(con, indicator, geography, period_filter)
        filtered = filter_requested_rows(question, rows)
        if not filtered:
            continue
        quality = sum(row.get("confidence", 0) for row in filtered) / len(filtered)
        if quality > best_quality + 1e-9:
            best_quality = quality
            result = filtered
        if best_quality >= 0.999:
            break
    if period_filter and period_filter[0] == "last" and result:
        return result[-int(period_filter[1]):]
    if any(term in normalized(question) for term in ("recemment", "recently")):
        return result[-12:]
    return result


def citation(row: dict) -> str:
    return f"({row['source_doc']}, p. {row['source_page']})"


def public_rows(rows: list[dict]) -> list[dict]:
    return [
        {key: value for key, value in row.items() if key != "confidence"}
        for row in rows
    ]

def clean_label(label: str, geography: str) -> str:
    """Strip a trailing geography mention already baked into the extracted label
    (e.g. 'Variation des prix - Togo') so it isn't repeated when we append
    'au {geography}' ourselves."""
    label = label.strip()
    for sep in (" - ", " – ", ", ", " ("):
        suffix = f"{sep}{geography}"
        if label.endswith(suffix):
            label = label[: -len(suffix)].rstrip(" (")
            break
        if label.endswith(f"{sep}{geography})"):
            label = label[: -len(f"{sep}{geography})")].rstrip()
            break
    return label.strip()


def cleaned_row_label(row: dict) -> str:
    return clean_label(str(row.get("indicator_label", "")), str(row.get("geography", "")))


def canonical_label(rows: list[dict]) -> str:
    """Choose one stable series label: longest shared label, then frequency, then alphabetic."""
    labels = [cleaned_row_label(row) for row in rows]
    labels = [label for label in labels if label]
    if not labels:
        return ""
    unique = sorted(set(labels), key=lambda label: (normalized(label), label))
    shared = [
        label
        for label in unique
        if all(normalized(label) in normalized(other) for other in unique)
    ]
    if shared:
        return min(shared, key=lambda label: (-len(normalized(label)), normalized(label), label))
    counts = {label: labels.count(label) for label in unique}
    top_frequency = max(counts.values())
    return min(
        (label for label in unique if counts[label] == top_frequency),
        key=lambda label: (normalized(label), label),
    )


def label_with_geography(label: str, geography: str) -> str:
    if geography and normalized(geography) in normalized(label):
        return label
    return f"{label} au {geography}"


def fallback_answer(question: str, rows: list[dict]) -> str:
    if not rows:
        return "Aucune donnée correspondante n'est disponible dans la base actuelle."
    first, latest = rows[0], rows[-1]
    label = canonical_label(rows)
    display_label = label_with_geography(label, first["geography"])
    prefix = "Série disponible"
    if "FIXTURE" in first["source_doc"]:
        prefix = "Donnée synthétique de développement — ne pas utiliser en production"
    elif "chiffre" in normalized(question) and "services" in first["indicator"]:
        prefix = "Donnée disponible la plus proche (services, pas industrie)"
    if len(rows) == 1:
        return (
            f"{prefix} : {display_label}. "
            f"{first['period']} : {first['value']:g} {first['unit']} {citation(first)}."
        )
    if len(rows) == 2:
        return (
            f"{prefix} : {display_label}. "
            f"{first['period']} : {first['value']:g} {first['unit']} {citation(first)} ; "
            f"{latest['period']} : {latest['value']:g} {latest['unit']} {citation(latest)}."
        )
    minimum = min(rows, key=lambda row: row["value"])
    maximum = max(rows, key=lambda row: row["value"])
    return (
        f"{prefix} : {display_label}. "
        f"Couverture : {len(rows)} observations de {first['period']} à "
        f"{latest['period']} {citation(first)} {citation(latest)}. "
        f"Première valeur : {first['value']:g} {first['unit']} {citation(first)} ; "
        f"dernière valeur : {latest['value']:g} {latest['unit']} {citation(latest)} ; "
        f"minimum : {minimum['value']:g} {minimum['unit']} {citation(minimum)} ; "
        f"maximum : {maximum['value']:g} {maximum['unit']} {citation(maximum)}."
    )


def chart_details(rows: list[dict]) -> tuple[str, str]:
    rows = dedupe_by_period(pick_headline_rows(rows))
    series = {(row["indicator"], row["geography"]) for row in rows}
    if len(rows) < 3 or len(series) != 1:
        return "none", ""
    row = rows[0]
    label = canonical_label(rows)
    title = f"{label} — {row['geography']} ({row['unit']})"
    return "line", title


def fallback_response(question: str, con: sqlite3.Connection) -> dict:
    rows = select_fallback_rows(con, question)
    label = canonical_label(rows)
    chart, title = chart_details(rows)
    return {
        "answer": fallback_answer(question, rows),
        "rows": public_rows(rows),
        "chart": chart,
        "title": title,
        "label": label,
    }


ROUTER_PROMPT = """You translate a question about West African economies into a SQLite query over:
observations(indicator, indicator_label, geography, period, value, unit, source_doc, source_page, confidence)

Known indicators: {indicators}

Return strict JSON: {{"sql": "SELECT ...", "chart": "line|bar|none", "title": "..."}}
Rules: SELECT-only, LIMIT 200, ORDER BY period. Match geography and indicator loosely (LIKE). If the question is not answerable from the schema, return {{"sql": null, "chart": "none", "title": ""}}.
When several indicator_label variants share the same indicator and period, prefer the overall or aggregate figure (labels containing 'global', 'ensemble', or 'IHPC au <pays>') unless the question names a specific category.
Never describe these figures using statistical language such as 'confidence interval', 'margin of error', or 'at the 95% confidence level' — you only have point estimates from official publications, not sampling distributions.

Question: {question}"""

ANSWER_PROMPT = """You are Sika, an assistant for official West African statistics. Answer the user's question using ONLY the data rows and passages provided. Cite every figure as (source_doc, p. page). Answer in the user's language (French or English). If data is missing, say so plainly. Be precise and concise.

Never describe these figures using statistical language such as 'confidence interval', 'margin of error', or 'at the 95% confidence level' — you only have point estimates from official publications, not sampling distributions.

Question: {question}

Data rows:
{rows}

Context passages:
{passages}"""


@app.post("/ask")
def ask(q: Ask):
    try:
        con = db()
        con.execute("SELECT 1 FROM observations LIMIT 1")
    except sqlite3.Error:
        return {
            "answer": "La base de données est vide ou indisponible. Chargez les sources puis réessayez.",
            "rows": [],
            "chart": "none",
            "title": "",
            "label": "",
        }
    if client is None:
        response = fallback_response(q.question, con)
        con.close()
        return response
    indicators = [
        row["indicator"]
        for row in con.execute("SELECT DISTINCT indicator FROM observations LIMIT 100")
    ]
    route = json.loads(
        client.chat.completions.create(
            model=MODEL,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "user",
                    "content": ROUTER_PROMPT.format(
                        indicators=", ".join(indicators), question=q.question
                    ),
                }
            ],
        ).choices[0].message.content
    )

    rows = []
    if route.get("sql") and route["sql"].strip().lower().startswith("select"):
        try:
            rows = [dict(row) for row in con.execute(route["sql"]).fetchall()]
        except sqlite3.Error:
            rows = []
    rows = filter_requested_rows(q.question, rows)
    label = canonical_label(rows)

    terms = [word for word in q.question.split() if len(word) > 4][:4]
    like = " OR ".join("text LIKE ?" for _ in terms) or "1=0"
    passages = [
        dict(row)
        for row in con.execute(
            f"""SELECT source_doc, page, substr(text,1,600) AS text
                FROM passages WHERE {like} LIMIT 4""",
            [f"%{term}%" for term in terms],
        ).fetchall()
    ]

    answer = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": ANSWER_PROMPT.format(
                    question=q.question,
                    rows=json.dumps(public_rows(rows)[:80], ensure_ascii=False),
                    passages=json.dumps(passages, ensure_ascii=False),
                ),
            }
        ],
    ).choices[0].message.content
    con.close()
    return {
        "answer": answer,
        "rows": public_rows(rows),
        "chart": route.get("chart", "none"),
        "title": (
            f"{label} — {rows[0]['geography']} ({rows[0]['unit']})"
            if rows and route.get("chart", "none") != "none"
            else ""
        ),
        "label": label,
    }


class BriefReq(BaseModel):
    topic: str
    geography: str = "Togo"


BRIEF_PROMPT = """You are a senior economist. Using ONLY the data rows below, write a one-page professional economic brief in French on '{topic}' for {geography}: title, 3 short sections (situation, dynamics, outlook/risks), each figure cited as (source, p. page). End with a 3-bullet executive summary. Markdown format.

Never describe these figures using statistical language such as 'confidence interval', 'margin of error', or 'at the 95% confidence level' — you only have point estimates from official publications, not sampling distributions.

Data:
{rows}"""


def select_brief_rows(
    con: sqlite3.Connection, topic: str, geography: str
) -> list[dict]:
    indicators = fallback_indicators(topic)
    if indicators:
        marks = ",".join("?" for _ in indicators)
        return [
            dict(row)
            for row in con.execute(
                f"""SELECT * FROM observations WHERE geography = ?
                    AND confidence >= 0.5 AND indicator IN ({marks})
                    ORDER BY period LIMIT 150""",
                (geography, *indicators),
            ).fetchall()
        ]
    return [
        dict(row)
        for row in con.execute(
            """SELECT * FROM observations WHERE geography = ?
               AND confidence >= 0.5
               AND (indicator_label LIKE ? OR indicator LIKE ?)
               ORDER BY period LIMIT 150""",
            (geography, f"%{topic}%", f"%{topic}%"),
        ).fetchall()
    ]


def brief_series(rows: list[dict]) -> list[list[dict]]:
    grouped: dict[tuple[str, str, str, str], list[dict]] = {}
    for row in rows:
        key = (
            row["indicator"],
            row["geography"],
            row["unit"],
            cleaned_row_label(row),
        )
        grouped.setdefault(key, []).append(row)

    def is_headline(group: list[dict]) -> bool:
        label = normalized(group[0]["indicator_label"])
        return any(hint in label for hint in HEADLINE_HINTS)

    return sorted(grouped.values(), key=lambda g: (not is_headline(g), -len(g)))


def series_key_figure(rows: list[dict]) -> str:
    first, latest = rows[0], rows[-1]
    minimum = min(rows, key=lambda row: row["value"])
    maximum = max(rows, key=lambda row: row["value"])
    return (
        f"- {first['indicator_label']} ({first['unit']}, {len(rows)} observations, "
        f"{first['period']}–{latest['period']}) : début {first['value']:g} "
        f"{first['unit']} {citation(first)} ; fin {latest['value']:g} "
        f"{latest['unit']} {citation(latest)} ; minimum {minimum['value']:g} "
        f"{minimum['unit']} {citation(minimum)} ; maximum {maximum['value']:g} "
        f"{maximum['unit']} {citation(maximum)}."
    )


def deterministic_brief(req: BriefReq, rows: list[dict], geography: str) -> str:
    title = f"# Note économique — {req.topic.strip().capitalize()} — {geography}"
    label = "**synthèse automatique sans analyse LLM**"
    if not rows:
        return (
            f"{title}\n\n{label}\n\n## Couverture\n"
            "Aucune observation correspondante n'est disponible dans la base.\n\n"
            "## Lacunes des données\nLe sujet demandé n'est pas couvert par les "
            "sources actuellement ingérées ; aucun chiffre n'est estimé ou inventé."
        )
    first, latest = rows[0], rows[-1]
    series = brief_series(rows)
    figures = "\n".join(series_key_figure(group) for group in series[:3])
    extra = len(series) - 3
    gaps = [
        "Aucune valeur manquante n'est interpolée et aucune causalité n'est déduite.",
        f"La couverture dépend des {len(set(row['source_doc'] for row in rows))} "
        "documents correspondant au filtre demandé.",
    ]
    if extra > 0:
        gaps.append(f"{extra} série(s) supplémentaire(s) ne sont pas détaillées ici.")
    missing_pages = sum(row.get("source_page") is None for row in rows)
    if missing_pages:
        gaps.append(f"{missing_pages} observation(s) n'ont pas de page source renseignée.")
    gap_text = "\n".join(f"- {gap}" for gap in gaps)
    return (
        f"{title}\n\n{label}\n\n## Couverture\n{len(rows)} observations, "
        f"de {first['period']} à {latest['period']} {citation(first)} "
        f"{citation(latest)}.\n\n## Chiffres clés\n{figures}\n\n"
        f"## Lacunes des données\n{gap_text}"
    )


@app.post("/brief")
def brief(req: BriefReq):
    geography = parse_geography(req.topic) or req.geography
    try:
        con = db()
        rows = select_brief_rows(con, req.topic, geography)
        con.close()
    except sqlite3.Error:
        rows = []
    fallback = {
        "brief": deterministic_brief(req, rows, geography),
        "n_observations": len(rows),
    }
    if client is None or not rows:
        return fallback
    try:
        text = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": BRIEF_PROMPT.format(
                        topic=req.topic,
                        geography=geography,
                        rows=json.dumps(public_rows(rows), ensure_ascii=False),
                    ),
                }
            ],
        ).choices[0].message.content
    except (APIError, APITimeoutError):
        return fallback
    return {"brief": text, "n_observations": len(rows)}


@app.get("/indicators")
def indicators():
    try:
        con = db()
        out = [
            dict(row)
            for row in con.execute(
                """SELECT indicator, indicator_label, geography,
                          COUNT(*) AS n, MIN(period) AS from_p, MAX(period) AS to_p
                   FROM observations
                   GROUP BY indicator, geography ORDER BY n DESC"""
            )
        ]
        con.close()
        return out
    except sqlite3.Error:
        return []

@app.get("/sources")
def sources():
    try:
        con = db()
        rows = con.execute(
            """SELECT source_doc, COUNT(*) AS observations,
                      COUNT(DISTINCT source_page) AS pages,
                      MIN(period) AS from_p, MAX(period) AS to_p
               FROM observations
               WHERE source_doc NOT LIKE 'FIXTURE%'
               GROUP BY source_doc ORDER BY observations DESC"""
        )
        result = []
        for row in rows:
            item = dict(row)
            metadata = SOURCE_METADATA.get(
                item["source_doc"],
                {
                    "publisher": "Source officielle",
                    "title": item["source_doc"],
                    "url": None,
                },
            )
            result.append({**item, **metadata})
        con.close()
        return result
    except sqlite3.Error:
        return []

app.mount("/", StaticFiles(directory="app", html=True), name="app")
