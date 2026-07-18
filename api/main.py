"""Sika API: cited answers and generated briefs over official statistics."""
import json
import os
import sqlite3

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()
client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6")
DB_PATH = os.getenv("SIKA_DB", "data/processed/sika.db")

app = FastAPI(title="Sika", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


class Ask(BaseModel):
    question: str


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
    con = db()
    indicators = [r["indicator"] for r in con.execute(
        "SELECT DISTINCT indicator FROM observations LIMIT 100")]
    route = json.loads(client.chat.completions.create(
        model=MODEL, response_format={"type": "json_object"},
        messages=[{"role": "user", "content": ROUTER_PROMPT.format(
            indicators=", ".join(indicators), question=q.question)}],
    ).choices[0].message.content)

    rows = []
    if route.get("sql") and route["sql"].strip().lower().startswith("select"):
        try:
            rows = [dict(r) for r in con.execute(route["sql"]).fetchall()]
        except sqlite3.Error:
            rows = []

    terms = [w for w in q.question.split() if len(w) > 4][:4]
    like = " OR ".join("text LIKE ?" for _ in terms) or "1=0"
    passages = [dict(r) for r in con.execute(
        f"SELECT source_doc, page, substr(text,1,600) AS text FROM passages WHERE {like} LIMIT 4",
        [f"%{t}%" for t in terms]).fetchall()]

    answer = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": ANSWER_PROMPT.format(
            question=q.question,
            rows=json.dumps(rows[:80], ensure_ascii=False),
            passages=json.dumps(passages, ensure_ascii=False))}],
    ).choices[0].message.content
    con.close()
    return {"answer": answer, "rows": rows, "chart": route.get("chart", "none"),
            "title": route.get("title", "")}


class BriefReq(BaseModel):
    topic: str
    geography: str = "Togo"


BRIEF_PROMPT = """You are a senior economist. Using ONLY the data rows below, write a one-page professional economic brief in French on '{topic}' for {geography}: title, 3 short sections (situation, dynamics, outlook/risks), each figure cited as (source, p. page). End with a 3-bullet executive summary. Markdown format.

Data:
{rows}"""


@app.post("/brief")
def brief(req: BriefReq):
    con = db()
    rows = [dict(r) for r in con.execute(
        """SELECT * FROM observations WHERE geography LIKE ?
           AND (indicator_label LIKE ? OR indicator LIKE ?)
           ORDER BY period LIMIT 150""",
        (f"%{req.geography}%", f"%{req.topic}%", f"%{req.topic}%")).fetchall()]
    text = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": BRIEF_PROMPT.format(
            topic=req.topic, geography=req.geography,
            rows=json.dumps(rows, ensure_ascii=False))}],
    ).choices[0].message.content
    con.close()
    return {"brief": text, "n_observations": len(rows)}


@app.get("/indicators")
def indicators():
    con = db()
    out = [dict(r) for r in con.execute(
        """SELECT indicator, indicator_label, geography,
                  COUNT(*) AS n, MIN(period) AS from_p, MAX(period) AS to_p
           FROM observations GROUP BY indicator, geography ORDER BY n DESC""")]
    con.close()
    return out


app.mount("/", StaticFiles(directory="app", html=True), name="app")
