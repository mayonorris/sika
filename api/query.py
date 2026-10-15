"""Default-disabled, fixture-confined natural-language query integration.

Trusted test applications inject settings; HTTP clients cannot enable this route,
choose files, supply SQL, or bypass the fresh publication proof in execute_query.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
import json
import logging
import math
from pathlib import Path
import re
import threading
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.requests import ClientDisconnect

from sika.interpretation import (
    InterpretationError, InterpretResult, interpret_question, parse_question_request,
)
from sika.query import QueryError
from sika.query_execution import ExecutionLimits, QueryResult, execute_query

MAX_REQUEST_BYTES = 8192
BODY_TIMEOUT_SECONDS = 2.0
HTTP_TIMEOUT_SECONDS = 6.0
_REPO = Path(__file__).resolve().parent.parent
_REAL_DATABASES = tuple(_REPO / "data" / "processed" / name
                        for name in ("sika.db", "sika_v2.db"))
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueryFixtureSettings:
    """Trusted application configuration, never an HTTP request model."""

    fixture_root: Path
    target: Path
    source_root: Path | None = None
    legacy: Path | None = None
    execution_limits: ExecutionLimits = field(default_factory=ExecutionLimits)
    today: date | None = None


def _settings(value: QueryFixtureSettings, *, existing: bool) -> QueryFixtureSettings:
    """Recheck confinement, aliases and lowerable policies, including per read."""
    try:
        if type(value) is not QueryFixtureSettings:
            raise ValueError
        if not isinstance(value.fixture_root, Path) or not isinstance(value.target, Path):
            raise ValueError
        if value.today is not None and type(value.today) is not date:
            raise ValueError
        root = value.fixture_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError
        resolved = []
        source_root = value.source_root or value.target.parent / "trust_core_sources"
        for item in (value.target, source_root, value.legacy):
            if item is None:
                resolved.append(None)
                continue
            if not isinstance(item, Path):
                raise ValueError
            path = item.resolve(strict=existing)
            if not path.is_relative_to(root):
                raise ValueError
            if any(path == real.resolve() or
                   (path.exists() and real.exists() and path.samefile(real))
                   for real in _REAL_DATABASES):
                raise ValueError
            resolved.append(path)
        target, sources, legacy = resolved
        if existing and (not target.is_file() or
                         (sources is not None and not sources.is_dir()) or
                         (legacy is not None and not legacy.is_file())):
            raise ValueError
        policy = ExecutionLimits.model_validate(value.execution_limits)
        return QueryFixtureSettings(root, target, sources, legacy, policy, value.today)
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError, ValidationError):
        raise ValueError("Invalid fixture query configuration") from None


_MESSAGES = {
    "query_disabled": ("Cette interface est désactivée ; le pilote utilise uniquement des fixtures.",
                       "This interface is disabled; the pilot uses fixtures only."),
    "invalid_question": ("Question invalide. Vérifiez les champs et les types autorisés.",
                         "Invalid question. Check the permitted fields and types."),
    "invalid_json": ("Le corps doit être un objet JSON UTF-8 valide, sans clés dupliquées.",
                     "Send a valid UTF-8 JSON object without duplicate keys."),
    "unsupported_media_type": ("Utilisez application/json en UTF-8, sans compression.",
                               "Use uncompressed UTF-8 application/json."),
    "request_too_large": ("La requête dépasse la limite autorisée.", "The request exceeds the byte limit."),
    "request_timeout": ("La lecture de la requête a expiré.", "Reading the request timed out."),
    "clarification_required": ("Précisez le pays et la période dans la question.",
                               "Specify the geography and period in the question."),
    "unsupported_question": ("Formulation non prise en charge. Précisez une série d’inflation, un pays et une période.",
                             "Unsupported wording. Specify an inflation series, geography and period."),
    "invalid_period": ("Vérifiez les dates, leur ordre et leur fréquence.",
                       "Check the dates, their order and frequency."),
    "period_range_too_large": ("La période demandée est trop longue.", "The requested period range is too long."),
    "unknown_series": ("Aucune série publiée ne correspond à cette demande.",
                       "No published series matches this request."),
    "ambiguous_series": ("Plusieurs séries correspondent. Précisez la variante de source ou la base de l’indice.",
                         "Several series match. Specify the source variant or index basis."),
    "publication_unavailable": ("Les preuves de publication ne permettent pas de restituer ces données.",
                                "Publication evidence does not authorize returning these data."),
    "database_unavailable": ("Les données du pilote sont indisponibles.", "The pilot data are unavailable."),
    "invalid_catalog": ("Le catalogue publié n’est pas disponible sous une forme valide.",
                        "The published catalog is not available in a valid form."),
    "invalid_intent": ("La demande structurée n’est pas valide.", "The structured query is invalid."),
    "invalid_execution_limits": ("La politique de vérification est indisponible.",
                                 "The verification policy is unavailable."),
    "query_busy": ("Une vérification est déjà en cours. Réessayez dans un instant.",
                   "A verification is already running. Try again shortly."),
    "query_timeout": ("La vérification a dépassé le délai d’attente.", "Verification exceeded the wait budget."),
    "response_too_large": ("La réponse dépasse la limite autorisée. Réduisez la demande.",
                           "The response exceeds the byte limit. Narrow the request."),
    "resource_limit": ("La vérification dépasse les ressources autorisées.",
                       "Verification exceeds the permitted resource budget."),
    "internal_error": ("La requête n’a pas pu être traitée.", "The request could not be processed."),
}


def _encode(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _response(payload: dict, status: int, request_id: str, *, retry: bool = False) -> Response:
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
               "X-Request-ID": request_id}
    if retry:
        headers["Retry-After"] = "1"
    return Response(content=_encode(payload), status_code=status,
                    media_type="application/json", headers=headers)


def _error(code: str, status: int, language: str, request_id: str,
           fields: tuple[str, ...] = ()) -> Response:
    if code not in _MESSAGES:
        code = "internal_error"
        status = 500
    detail = {"code": code, "message": _MESSAGES[code][language == "en"]}
    safe_fields = [name for name in fields if name in {"geography", "period", "price_basis", "source_variant"}]
    if safe_fields:
        detail["fields"] = safe_fields
    _LOG.info("query_refused request_id=%s code=%s", request_id, code)
    return _response({"version": 1, "status": "error", "rows": [], "row_count": 0,
                      "has_more": False, "complete": False, "error": detail,
                      "language": language, "request_id": request_id}, status, request_id,
                     retry=code == "query_busy")


def _language(payload: object) -> str:
    if isinstance(payload, dict):
        if payload.get("language") in ("fr", "en"):
            return payload["language"]
        question = payload.get("question")
        if isinstance(question, str) and re.search(r"\b(how|what|show|since|from|forecast|in)\b", question, re.I):
            return "en"
    return "fr"


class _TransportError(Exception):
    def __init__(self, code: str, status: int) -> None:
        self.code, self.status = code, status


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise ValueError("Nonfinite number")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite number")
    return result


async def _body(request: Request) -> object:
    headers = request.headers
    if request.scope.get("query_string", b""):
        raise _TransportError("invalid_question", 422)
    media = headers.getlist("content-type")
    if (len(media) != 1 or not re.fullmatch(
            r"application/json(?:\s*;\s*charset\s*=\s*utf-8)?", media[0], re.I)):
        raise _TransportError("unsupported_media_type", 415)
    encoding = headers.getlist("content-encoding")
    if encoding and (len(encoding) != 1 or encoding[0].lower() != "identity"):
        raise _TransportError("unsupported_media_type", 415)
    length = headers.getlist("content-length")
    if length:
        if len(length) != 1 or not re.fullmatch(r"[0-9]{1,9}", length[0], re.ASCII):
            raise _TransportError("invalid_json", 400)
        if int(length[0]) > MAX_REQUEST_BYTES:
            raise _TransportError("request_too_large", 413)
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > MAX_REQUEST_BYTES:
            raise _TransportError("request_too_large", 413)
        chunks.extend(chunk)
    try:
        return json.loads(chunks.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_nonfinite, parse_float=_finite_float)
    except (ValueError, UnicodeError, RecursionError):
        raise _TransportError("invalid_json", 400) from None


def _answer(result: QueryResult, language: str) -> str:
    """Describe returned rows, not a merged vintage or gap-free historical series."""
    if not result.rows:
        return ("Aucune observation publiée dans la période demandée." if language == "fr" else
                "No published observation in the requested period.")
    def figure(row) -> str:
        unit = "%" if row.unit == "percent" else "index"
        return f"{row.period} : {row.value} {unit} ({row.citation.source_doc}, p. {row.citation.source_page})"
    figures = figure(result.rows[0])
    if len(result.rows) > 1:
        figures += "; " + figure(result.rows[-1])
    if language == "fr":
        text = f"{result.row_count} observations publiées restituées. Première et dernière lignes : {figures}."
        text += " La couverture peut comporter des lacunes."
        if result.has_more:
            text += " Résultat tronqué : seule une partie des observations est affichée."
        if "multiple_vintages" in result.warnings:
            text += " Les différentes versions de publication sont conservées séparément."
    else:
        text = f"{result.row_count} published observations returned. First and last rows: {figures}."
        text += " Coverage may contain gaps."
        if result.has_more:
            text += " Truncated result: only a subset of observations is shown."
        if "multiple_vintages" in result.warnings:
            text += " Different publication vintages are kept separate."
    return text


def build_query_router(settings: QueryFixtureSettings | None = None) -> APIRouter:
    """Build the isolated pilot; settings=None cannot be enabled by environment."""
    configuration = _settings(settings, existing=False) if settings is not None else None
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sika-fixture-query") if configuration else None
    gate = threading.BoundedSemaphore(1)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if pool is not None:
                # Shutdown waits for real cleanup; cancellation cannot kill a parser.
                await asyncio.to_thread(pool.shutdown, wait=True, cancel_futures=False)

    router = APIRouter(lifespan=lifespan)

    def execute(interpreted: InterpretResult, request_id: str) -> Response:
        try:
            try:
                current = _settings(configuration, existing=True)
            except ValueError:
                return _error("database_unavailable", 503, interpreted.language, request_id)
            result = execute_query(interpreted.intent, current.target, legacy=current.legacy,
                                   source_root=current.source_root, limits=current.execution_limits)
            payload = result.model_dump(mode="json")
            payload.update(language=interpreted.language, answer=_answer(result, interpreted.language),
                           interpretation=interpreted.model_dump(mode="json"), request_id=request_id)
            if len(_encode(payload)) > current.execution_limits.max_result_bytes:
                return _error("response_too_large", 413, interpreted.language, request_id)
            return _response(payload, 200, request_id)
        except QueryError as exc:
            status = (504 if exc.code == "query_timeout" else 413 if exc.code == "response_too_large"
                      else 422 if exc.code in {"unknown_series", "ambiguous_series", "invalid_intent",
                                               "invalid_period", "period_range_too_large"} else 503)
            return _error(exc.code, status, interpreted.language, request_id)
        except Exception:
            # HTTP boundary: never leak source contents, paths or underlying errors.
            return _error("internal_error", 500, interpreted.language, request_id)
        finally:
            gate.release()

    @router.post("/v1/query")
    async def query(request: Request) -> Response:
        request_id, language = str(uuid4()), "fr"
        if configuration is None:
            return _error("query_disabled", 503, language, request_id)
        try:
            payload = await asyncio.wait_for(_body(request), timeout=BODY_TIMEOUT_SECONDS)
            language = _language(payload)
            parsed = parse_question_request(payload)
            interpreted = interpret_question(parsed, today=configuration.today)
            language = interpreted.language
        except _TransportError as exc:
            return _error(exc.code, exc.status, language, request_id)
        except asyncio.TimeoutError:
            return _error("request_timeout", 408, language, request_id)
        except ClientDisconnect:
            return _error("invalid_json", 400, language, request_id)
        except InterpretationError as exc:
            return _error(exc.code, 422, language, request_id, exc.fields)
        if not gate.acquire(blocking=False):
            return _error("query_busy", 429, language, request_id)
        try:
            future = pool.submit(execute, interpreted, request_id)
        except RuntimeError:
            gate.release()
            return _error("database_unavailable", 503, language, request_id)
        # Shield both cancellation paths. Only the physical worker releases gate.
        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout=HTTP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return _error("query_timeout", 504, language, request_id)

    return router
