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
client = OpenAI(api_key=API_KEY) if API_KEY and "your-key" not in API_KEY else None
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


def normalized(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plain.lower()).strip()


def fallback_indicators(question: str) -> tuple[str, ...]:
    clean = normalized(question)
    for terms, indicators in INDICATOR_RULES:
        if any(term in clean for term in terms):
            return indicators
    return ()


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
    since = re.search(r"(?:depuis|since)\s+(20\d{2})", normalized(question))
    params: list[object] = [indicator, "Togo"]
    period_clause = ""
    if since:
        period_clause = " AND period >= ?"
        params.append(since.group(1))
    rows = con.execute(
        f"""SELECT indicator, indicator_label, geography, period, value, unit,
                   source_doc, source_page, confidence
            FROM observations WHERE indicator = ? AND geography = ?
              AND confidence >= 0.5{source_clause}{period_clause}
            ORDER BY period LIMIT 200""",
        params,
    ).fetchall()
    result = [dict(row) for row in rows]
    if any(term in normalized(question) for term in ("recemment", "recently")):
        return result[-12:]
    return result


def citation(row: dict) -> str:
    return f"({row['source_doc']}, p. {row['source_page']})"


def fallback_answer(question: str, rows: list[dict]) -> str:
    if not rows:
        return "Aucune donnée correspondante n'est disponible dans la base actuelle."
    first, latest = rows[0], rows[-1]
    prefix = "Série disponible"
    if "FIXTURE" in first["source_doc"]:
        prefix = "Donnée synthétique de développement — ne pas utiliser en production"
    elif "chiffre" in normalized(question) and "services" in first["indicator"]:
        prefix = "Donnée disponible la plus proche (services, pas industrie)"
    return (
        f"{prefix} : {first['indicator_label']} au {first['geography']}. "
        f"{first['period']} : {first['value']:g} {first['unit']} {citation(first)} ; "
        f"{latest['period']} : {latest['value']:g} {latest['unit']} {citation(latest)}."
    )


def chart_details(rows: list[dict]) -> tuple[str, str]:
    series = {(row["indicator"], row["geography"]) for row in rows}
    if len(rows) < 3 or len(series) != 1:
        return "none", ""
    row = rows[0]
    title = f"{row['indicator_label']} — {row['geography']} ({row['unit']})"
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


@app.post("/brief")
def brief(req: BriefReq):
    if client is None:
        return {
            "brief": "La génération de briefs nécessite le service d'analyse. Réessayez lorsqu'il est disponible.",
            "n_observations": 0,
        }
    con = db()
    rows = [
        dict(row)
        for row in con.execute(
            """SELECT * FROM observations WHERE geography LIKE ?
               AND (indicator_label LIKE ? OR indicator LIKE ?)
               ORDER BY period LIMIT 150""",
            (f"%{req.geography}%", f"%{req.topic}%", f"%{req.topic}%"),
        ).fetchall()
    ]
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
    con.close()
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


app.mount("/", StaticFiles(directory="app", html=True), name="app")
