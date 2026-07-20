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
}


INDICATOR_RULES = (
    (("inflation", "ihpc", "prix"), ("inflation_rate_yoy",)),
    (
        ("chiffre d'affaires", "chiffre affaires", "turnover"),
        ("turnover_index_industry", "turnover_index_services"),
    ),
    (
        ("production industrielle", "industrial production", "ipi"),
        ("industrial_production_index",),
    ),
    (
        ("microfinance", "depot", "credit"),
        ("deposits_microfinance", "credit_microfinance"),
    ),
    (("taux directeur", "policy rate"), ("policy_rate",)),
)


HEADLINE_HINTS = (
    "global",
    "ensemble",
    "ihpc au togo",
    "variation des prix depuis 12 mois",
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
    "togo", "uemoa", "benin", "burkina faso", "cote d'ivoire",
    "guinee bissau", "mali", "niger", "senegal",
)


def normalized(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plain.lower()).strip()


def fallback_indicators(question: str) -> tuple[str, ...]:
    clean = normalized(question)
    for terms, indicators in INDICATOR_RULES:
        if any(term in clean for term in terms):
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


def dedupe_by_period(rows: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    for row in rows:
        current = best.get(row["period"])
        if current is None or row.get("confidence", 0) > current.get("confidence", 0):
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


def parse_period(question: str) -> tuple[str, str] | None:
    clean = normalized(question)
    iso = re.search(r"\b(20\d{2}-(?:0[1-9]|1[0-2]))\b", clean)
    if iso:
        return "exact", iso.group(1)
    months = "|".join(MONTH_NUMBERS)
    named = re.search(rf"\b({months})\s+(20\d{{2}})\b", clean)
    if named:
        return "exact", f"{named.group(2)}-{MONTH_NUMBERS[named.group(1)]}"
    since = re.search(r"\b(?:depuis|since)\s+(20\d{2})\b", clean)
    if since:
        return "since", since.group(1)
    year = re.search(r"\ben\s+(20\d{2})\b", clean)
    if year:
        return "year", year.group(1)
    return None

def select_fallback_rows(con: sqlite3.Connection, question: str) -> list[dict]:
    candidates = fallback_indicators(question)
    if not candidates:
        return []
    availability = {
        row["indicator"]: (row["n"], row["real_n"])
        for row in con.execute(
            """SELECT indicator, COUNT(*) AS n,
                      SUM(CASE WHEN source_doc NOT LIKE 'FIXTURE%' THEN 1 ELSE 0 END) AS real_n
               FROM observations WHERE confidence >= 0.5 GROUP BY indicator"""
        )
    }
    indicator = next(
        (item for item in candidates if availability.get(item, (0, 0))[0] >= 3),
        candidates[0],
    )
    real_only = availability.get(indicator, (0, 0))[1] > 0
    source_clause = " AND source_doc NOT LIKE 'FIXTURE%'" if real_only else ""
    period_filter = parse_period(question)
    params: list[object] = [indicator, "Togo"]
    period_clause = ""
    if period_filter:
        mode, period = period_filter
        if mode == "exact":
            period_clause = " AND period = ?"
            params.append(period)
        elif mode == "year":
            period_clause = " AND period LIKE ?"
            params.append(f"{period}%")
        else:
            period_clause = " AND period >= ?"
            params.append(period)
    rows = con.execute(
        f"""SELECT indicator, indicator_label, geography, period, value, unit,
                   source_doc, source_page, confidence
            FROM observations WHERE indicator = ? AND geography = ?
              AND confidence >= 0.5{source_clause}{period_clause}
            ORDER BY period LIMIT 200""",
        params,
    ).fetchall()
    result = filter_requested_rows(question, [dict(row) for row in rows])
    if any(term in normalized(question) for term in ("recemment", "recently")):
        return result[-12:]
    return result


def citation(row: dict) -> str:
    return f"({row['source_doc']}, p. {row['source_page']})"


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


def fallback_answer(question: str, rows: list[dict]) -> str:
    if not rows:
        return "Aucune donnée correspondante n'est disponible dans la base actuelle."
    first, latest = rows[0], rows[-1]
    label = clean_label(first["indicator_label"], first["geography"])
    prefix = "Série disponible"
    if "FIXTURE" in first["source_doc"]:
        prefix = "Donnée synthétique de développement — ne pas utiliser en production"
    elif "chiffre" in normalized(question) and "services" in first["indicator"]:
        prefix = "Donnée disponible la plus proche (services, pas industrie)"
    if len(rows) <= 2:
        return (
            f"{prefix} : {label} au {first['geography']}. "
            f"{first['period']} : {first['value']:g} {first['unit']} {citation(first)} ; "
            f"{latest['period']} : {latest['value']:g} {latest['unit']} {citation(latest)}."
        )
    minimum = min(rows, key=lambda row: row["value"])
    maximum = max(rows, key=lambda row: row["value"])
    return (
        f"{prefix} : {label} au {first['geography']}. "
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
    label = clean_label(row["indicator_label"], row["geography"])
    title = f"{label} — {row['geography']} ({row['unit']})"
    return "line", title


def fallback_response(question: str, con: sqlite3.Connection) -> dict:
    rows = select_fallback_rows(con, question)
    chart, title = chart_details(rows)
    return {
        "answer": fallback_answer(question, rows),
        "rows": rows,
        "chart": chart,
        "title": title,
    }


ROUTER_PROMPT = """You translate a question about West African economies into a SQLite query over:
observations(indicator, indicator_label, geography, period, value, unit, source_doc, source_page, confidence)

Known indicators: {indicators}

Return strict JSON: {{"sql": "SELECT ...", "chart": "line|bar|none", "title": "..."}}
Rules: SELECT-only, LIMIT 200, ORDER BY period. Match geography and indicator loosely (LIKE). If the question is not answerable from the schema, return {{"sql": null, "chart": "none", "title": ""}}.
When several indicator_label variants share the same indicator and period, prefer the overall or aggregate figure (labels containing 'global', 'ensemble', or 'IHPC au <pays>') unless the question names a specific category.

Question: {question}"""

ANSWER_PROMPT = """You are Sika, an assistant for official West African statistics. Answer the user's question using ONLY the data rows and passages provided. Cite every figure as (source_doc, p. page). Answer in the user's language (French or English). If data is missing, say so plainly. Be precise and concise.

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
                    rows=json.dumps(rows[:80], ensure_ascii=False),
                    passages=json.dumps(passages, ensure_ascii=False),
                ),
            }
        ],
    ).choices[0].message.content
    con.close()
    return {
        "answer": answer,
        "rows": rows,
        "chart": route.get("chart", "none"),
        "title": route.get("title", ""),
    }


class BriefReq(BaseModel):
    topic: str
    geography: str = "Togo"


BRIEF_PROMPT = """You are a senior economist. Using ONLY the data rows below, write a one-page professional economic brief in French on '{topic}' for {geography}: title, 3 short sections (situation, dynamics, outlook/risks), each figure cited as (source, p. page). End with a 3-bullet executive summary. Markdown format.

Data:
{rows}"""


def select_brief_rows(con: sqlite3.Connection, req: BriefReq) -> list[dict]:
    return [
        dict(row)
        for row in con.execute(
            """SELECT * FROM observations WHERE geography LIKE ?
               AND confidence >= 0.5
               AND (indicator_label LIKE ? OR indicator LIKE ?)
               ORDER BY period LIMIT 150""",
            (f"%{req.geography}%", f"%{req.topic}%", f"%{req.topic}%"),
        ).fetchall()
    ]


def brief_series(rows: list[dict]) -> list[list[dict]]:
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        key = (row["indicator"], row["geography"], row["unit"])
        grouped.setdefault(key, []).append(row)
    return sorted(grouped.values(), key=len, reverse=True)


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


def deterministic_brief(req: BriefReq, rows: list[dict]) -> str:
    title = f"# Note économique — {req.topic.strip().capitalize()} — {req.geography}"
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
    try:
        con = db()
        rows = select_brief_rows(con, req)
        con.close()
    except sqlite3.Error:
        rows = []
    fallback = {"brief": deterministic_brief(req, rows), "n_observations": len(rows)}
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
                        geography=req.geography,
                        rows=json.dumps(rows, ensure_ascii=False),
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
