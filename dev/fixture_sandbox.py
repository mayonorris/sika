"""Disposable loopback-only S0.5.3 fixture app, never a production activation path.

All data, source profiles and review evidence are explicitly synthetic. Only this
isolated process simulates a test reviewer; no real OS operator is enrolled.
"""

import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Iterator, Mapping
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
import pdfplumber
import sqlalchemy as sa

from api.query import QueryFixtureSettings, build_query_router
from dev.fixture_data import ASSETS, CONCEPTS, FIXTURE_TODAY, SOURCE_VARIANT, observations, printed_row, series_definitions
from sika import publication, quality
from sika.database import create_database_engine

ROOT = Path(__file__).resolve().parents[1]
_REVIEWER = "test:local-fixture-sandbox"
_PUBLISHER = "SIKA SYNTHETIC FIXTURES - NOT OFFICIAL DATA"
_ASSET_LIMIT = 1024 * 1024


@dataclass(frozen=True)
class FixtureSandbox:
    root: Path
    target: Path
    source_root: Path
    fixture_id: str
    today: date
    port: int
    observation_count: int
    series_count: int
    sources: Mapping[str, Path]
    hashes: Mapping[str, str]


def _port(value: object) -> int:
    if type(value) is not int or not 1024 <= value <= 65535:
        raise ValueError("Expected a local development port between 1024 and 65535")
    return value


def _seed(sandbox: FixtureSandbox, rows: list[dict], profiles: dict) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    url = sa.URL.create("sqlite", database=str(sandbox.target))
    config.set_main_option("sqlalchemy.url", str(url).replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_database_engine(url)
    try:
        metadata = sa.MetaData()
        metadata.reflect(engine)
        tables = metadata.tables
        definitions = series_definitions()
        with engine.begin() as con:
            con.execute(tables["concepts"].insert(), [
                {"id": position, "code": concept, "label_fr": label_fr, "label_en": label_en}
                for position, (concept, _, label_fr, label_en) in enumerate(CONCEPTS, 1)
            ])
            con.execute(tables["geographies"].insert(), [
                {"id": 1, "code": "TG", "label_fr": "Togo", "label_en": "Togo"},
                {"id": 2, "code": "BJ", "label_fr": "Bénin", "label_en": "Benin"},
            ])
            con.execute(tables["units"].insert(), [
                {"id": 1, "code": "percent", "label_fr": "Pourcentage", "symbol": "%", "quantity": "rate", "scale": 1},
                {"id": 2, "code": "index", "label_fr": "Indice", "symbol": "index", "quantity": "index", "scale": 1},
            ])
            con.execute(tables["dimensions"].insert(), [
                {"id": position, "category": "all_items", "sector": "all_sectors",
                 "aggregation_level": "total", "price_basis": basis,
                 "seasonal_adjustment": "not_reported", "source_variant": SOURCE_VARIANT,
                 "description": "Invented local fixture; not an official methodology."}
                for position, basis in enumerate(("not_applicable", "index_2023", "index_2014"), 1)
            ])
            con.execute(tables["series"].insert(), [
                {"id": item["id"], "series_key": item["series_key"],
                 "concept_id": next(i for i, row in enumerate(CONCEPTS, 1) if row[0] == item["concept"]),
                 "geography_id": 1 if item["geography"] == "TG" else 2,
                 "unit_id": 2 if item["unit"] == "index" else 1,
                 "dimensions_id": {"not_applicable": 1, "index_2023": 2, "index_2014": 3}[item["price_basis"]],
                 "frequency": "monthly", "label_fr": item["label_fr"], "label_en": item["label_en"]}
                for item in definitions
            ])
            origin = f"http://127.0.0.1:{sandbox.port}"
            con.execute(tables["sources"].insert().values(
                id=1, source_key=f"fixture.{sandbox.fixture_id}", publisher=_PUBLISHER,
                title="Invented monthly inflation fixture publications", canonical_url=origin + "/",
                reuse_status="allowed", license_url=origin + "/fixture-license",
                attribution="SIKA invented local fixtures; NO official figures or real publication approval."))
            for release_id, asset in enumerate(ASSETS, 1):
                doc = next(name for name in sandbox.sources if name.endswith(f"_{asset}.pdf"))
                profile = profiles[doc]
                con.execute(tables["releases"].insert().values(
                    id=release_id, release_key=f"fixture.{sandbox.fixture_id}.{asset}", source_id=1,
                    source_doc=doc, source_url=profile["url"], publisher=_PUBLISHER,
                    content_sha256=profile["sha256"], published_on=date(2026, 10, 15),
                    retrieved_at=datetime.now(timezone.utc), storage_uri=sandbox.sources[doc].as_uri(),
                    media_type="application/pdf", supersedes_id=1 if asset == "revision" else None))
            con.execute(tables["observations"].insert(), [
                {"id": row["id"], "series_id": row["series_id"], "release_id": ASSETS.index(row["asset"]) + 1,
                 "period": row["period"], "value": row["value"], "source_page": row["source_page"],
                 "source_locator": row["source_locator"], "original_label": printed_row(row),
                 "confidence": None, "revision_status": row["revision_status"]}
                for row in rows
            ])
        for release_id, asset in enumerate(ASSETS, 1):
            report = quality.run_quality_checks(sandbox.target, source_root=sandbox.source_root,
                                               release_ids=[release_id])["releases"][0]
            if report["status"] != "passed":
                raise RuntimeError("Synthetic fixture QA failed; no server will start")
            evidence = {
                "source_values": "Every invented row was checked against the generated synthetic PDF text.",
                "revision_marks": "Final/revised labels are explicit synthetic fixture marks, not official revisions.",
                "reuse_rights": "These invented local-test fixtures contain no official source data.",
                "verified_observation_ids": [row["id"] for row in rows if row["asset"] == asset],
                "warnings": {item["code"]: "Explicitly acknowledged synthetic-only fixture limitation."
                             for item in report["findings"] if item["severity"] == "WARN"},
            }
            approved = publication.approve(sandbox.target, release_id, report["quality_run_id"],
                                           "Simulate review of the complete invented fixture only.", evidence,
                                           source_root=sandbox.source_root)
            publication.publish(sandbox.target, release_id, approved["review_decision_id"],
                                "Publish invented fixtures inside this isolated local test process only.",
                                source_root=sandbox.source_root)
    finally:
        engine.dispose()


@contextmanager
def fixture_environment(*, storage_parent: Path | None = None, port: int = 8001) -> Iterator[FixtureSandbox]:
    """Always create a new owned fixture root; never accept an existing DB target.

    The synthetic identity/allowlist and invented registry entries are test-process
    simulations scoped to this context. No real reviewer is resolved or enrolled.
    No publication guard, proof, validator or SQL authority function is patched.
    """
    port = _port(port)
    if storage_parent is not None and not isinstance(storage_parent, Path):
        raise ValueError("Expected a trusted fixture storage directory")
    parent = storage_parent or ROOT / "data" / "processed" / "fixture_sandbox"
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="s0_5_3_", dir=parent.resolve())).resolve(strict=True)
    source_root = root / "trust_core_sources"
    source_root.mkdir()
    fixture_id = uuid4().hex
    rows = observations()
    sources, hashes, profiles = {}, {}, {}
    for asset in ASSETS:
        template = Path(__file__).parent / "fixtures" / f"sika_synthetic_{asset}.pdf"
        data = template.read_bytes()
        if len(data) > _ASSET_LIMIT:
            raise ValueError("Synthetic fixture PDF exceeds the local byte limit")
        digest = hashlib.sha256(data).hexdigest()
        path = source_root / f"{digest}.pdf"
        path.write_bytes(data)
        selected = [row for row in rows if row["asset"] == asset]
        with pdfplumber.open(path) as pdf:
            expected_pages = max(row["source_page"] for row in selected)
            if len(pdf.pages) != expected_pages:
                raise ValueError("Synthetic fixture page count does not match its manifest")
            text = [page.extract_text() or "" for page in pdf.pages]
            if any("SIKA SYNTHETIC FIXTURES" not in page for page in text):
                raise ValueError("Synthetic fixture warning is missing")
            for row in selected:
                if printed_row(row) not in text[row["source_page"] - 1].splitlines():
                    raise ValueError("Synthetic fixture PDF values do not match their manifest")
        name = f"sika_synthetic_{fixture_id}_{asset}.pdf"
        sources[name], hashes[name] = path, digest
        profiles[name] = {"basis": 2014 if asset == "monthly_2014" else 2023,
                          "variant": SOURCE_VARIANT, "sha256": digest, "pages": expected_pages,
                          "url": f"http://127.0.0.1:{port}/fixture-source/{name}", "publisher": _PUBLISHER}
    sandbox = FixtureSandbox(root, root / "fixtures.db", source_root, fixture_id, FIXTURE_TODAY,
                             port, len(rows), len(series_definitions()),
                             MappingProxyType(sources), MappingProxyType(hashes))
    # No caller-controlled path/reviewer exists in this new owned fixture context.
    with ExitStack() as stack:
        stack.enter_context(patch.object(publication, "operator_identity", return_value=_REVIEWER))
        stack.enter_context(patch.dict(os.environ, {"SIKA_REVIEWER_IDS": _REVIEWER}))
        for name, profile in profiles.items():
            if name in quality.PROFILES:
                raise ValueError("Refuse to replace an existing source profile")
            stack.enter_context(patch.dict(quality.PROFILES, {name: profile}))
        _seed(sandbox, rows, profiles)
        (root / "fixture_run.json").write_text(json.dumps({
            "synthetic": True, "fixture_id": fixture_id, "today": str(FIXTURE_TODAY),
            "observations": len(rows), "series": sandbox.series_count,
            "warning": "INVENTED TEST DATA; NO REAL REVIEWER ENROLLED; NOT OFFICIAL STATISTICS",
        }, indent=2), encoding="utf-8")
        yield sandbox
    # Keep owned files for inspection. No recursive removal or existing-target reuse.


def create_app(sandbox: FixtureSandbox) -> FastAPI:
    """Serve one fixture page and allowlisted synthetic PDFs, never a DB directory."""
    if type(sandbox) is not FixtureSandbox:
        raise ValueError("Expected an owned synthetic fixture environment")
    router = build_query_router(QueryFixtureSettings(
        fixture_root=sandbox.root, target=sandbox.target, source_root=sandbox.source_root, today=sandbox.today))
    app = FastAPI(title="Sika synthetic fixture sandbox", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)
    origins = {f"http://127.0.0.1:{sandbox.port}", f"http://localhost:{sandbox.port}"}
    hosts = {f"127.0.0.1:{sandbox.port}", f"localhost:{sandbox.port}"}

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.headers.get("host", "").lower() not in hosts or (
                request.headers.get("origin") is not None and request.headers["origin"] not in origins):
            response = JSONResponse({"synthetic": True, "status": "error", "rows": [],
                                     "error": {"code": "local_only", "message": "Bac à sable local uniquement."}}, status_code=403)
        else:
            response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                 "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                                 "Content-Security-Policy": "default-src 'self'; connect-src 'self'; object-src 'none'; "
                                 "base-uri 'none'; frame-ancestors 'none'; form-action 'self'; "
                                 "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"})
        return response

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse((ROOT / "app" / "fixture_sandbox.html").read_text(encoding="utf-8"))

    @app.get("/sandbox-info")
    def information():
        return {"synthetic": True, "fixture_id": sandbox.fixture_id, "today": str(sandbox.today),
                "observation_count": sandbox.observation_count, "series_count": sandbox.series_count}

    @app.get("/fixture-license", response_class=PlainTextResponse)
    def license_notice():
        return ("SIKA SYNTHETIC FIXTURES - NOT OFFICIAL DATA\nInvented local testing data only. "
                "No INSEED/BCEAO publication, commercial reuse permission or real approval is represented.")

    @app.get("/fixture-source/{filename}")
    def source(filename: str):
        path = sandbox.sources.get(filename)
        if path is None:
            return Response(status_code=404)
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(sandbox.source_root) or resolved.stat().st_size > _ASSET_LIMIT:
                return Response(status_code=404)
            data = resolved.read_bytes()
            if len(data) > _ASSET_LIMIT or hashlib.sha256(data).hexdigest() != sandbox.hashes[filename]:
                return Response(status_code=404)
        except (OSError, ValueError):
            return Response(status_code=404)
        return Response(data, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{filename}"'})

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an isolated, synthetic-only Sika sandbox on loopback.")
    parser.add_argument("--port", type=int, default=8001)
    arguments = parser.parse_args()
    port = _port(arguments.port)
    import uvicorn

    with fixture_environment(port=port) as sandbox:
        print("SIKA SYNTHETIC SANDBOX - NO OFFICIAL DATA; NO PROVIDER CALLS", flush=True)
        print(f"Local test URL: http://127.0.0.1:{port}/", flush=True)
        print(f"Owned fixture directory: {sandbox.root}", flush=True)
        print(f"Frozen fixture clock: {sandbox.today}; observations: {sandbox.observation_count}", flush=True)
        uvicorn.run(create_app(sandbox), host="127.0.0.1", port=port, reload=False, workers=1, access_log=False)


if __name__ == "__main__":
    main()
