"""Closed, deterministic French/English grammar for the inflation query pilot.

Every supported word belongs to a full-match template. This is not an LLM or a
general language parser: unknown modifiers require clarification instead of being
discarded. Interpretation grants no catalog or publication access.
"""

from datetime import date, datetime, timezone
import re
from typing import Annotated, Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from sika.query import MAX_PERIODS, MAX_ROWS, QueryError, QueryIntent, SeriesSelector


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")


class QuestionFilters(_StrictModel):
    """Optional explicit disambiguation, never paths, policy, proof or SQL."""

    source_variant: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    price_basis: Literal["index_2014", "index_2023"] | None = None

    @field_validator("source_variant")
    @classmethod
    def coded_variant(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]*", value, re.ASCII):
            raise ValueError("Expected an explicit source-variant code")
        return value


class QuestionRequest(_StrictModel):
    version: Literal[1] = 1
    question: Annotated[str, Field(min_length=3, max_length=1000)]
    limit: Annotated[int, Field(ge=1, le=MAX_ROWS)] = 100
    language: Literal["auto", "fr", "en"] = "auto"
    filters: QuestionFilters = Field(default_factory=QuestionFilters)

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("Expected integer contract version 1")
        return value

    @field_validator("question")
    @classmethod
    def supported_text(cls, value: str) -> str:
        if any(unicodedata.category(char).startswith("C") for char in value):
            raise ValueError("Control characters are unsupported")
        value = value.strip()
        if len(value) < 3:
            raise ValueError("A question is required")
        return value


class InterpretResult(_StrictModel):
    intent: QueryIntent
    language: Literal["fr", "en"]
    assumptions: tuple[str, ...]
    interpreter_version: Literal["inflation-nl-v1"] = "inflation-nl-v1"


class InterpretationError(QueryError):
    """Safe codes/field names only; the original question is never echoed."""

    def __init__(self, code: str, message: str, fields: tuple[str, ...] = ()) -> None:
        super().__init__(code, message)
        self.fields = fields


def parse_question_request(payload: dict[str, object] | QuestionRequest) -> QuestionRequest:
    """Revalidate nested models, including constructed/copied instances."""
    try:
        return QuestionRequest.model_validate(payload)
    except (ValidationError, ValueError, TypeError):
        raise InterpretationError("invalid_question", "Question invalide / Invalid question.") from None


def _normalize(value: str) -> str:
    value = value.replace("’", "'").replace("œ", "oe").replace("Œ", "OE")
    value = value.replace("–", "-").replace("—", "-").replace("‑", "-")
    value = "".join(char for char in unicodedata.normalize("NFKD", value)
                    if not unicodedata.combining(char))
    # Never silently drop non-Latin text as ascii(..., errors='ignore') would.
    if not value.isascii():
        raise InterpretationError("unsupported_question", "Formulation non prise en charge / Unsupported wording.")
    return re.sub(r" +", " ", value.lower())


_CONCEPTS = {
    "fr": {
        "inflation": "inflation_rate_yoy", "taux d'inflation": "inflation_rate_yoy",
        "inflation en glissement annuel": "inflation_rate_yoy",
        "inflation annuelle": "inflation_rate_yoy", "glissement annuel des prix": "inflation_rate_yoy",
        "inflation mensuelle": "inflation_rate_mom", "variation mensuelle des prix": "inflation_rate_mom",
        "variation des prix sur trois mois": "inflation_rate_3m",
        "variation des prix sur 3 mois": "inflation_rate_3m",
        "inflation moyenne sur douze mois": "inflation_rate_12m_average",
        "inflation moyenne sur 12 mois": "inflation_rate_12m_average",
        "ihpc": "cpi_index", "indice des prix a la consommation": "cpi_index",
        "indice harmonise des prix a la consommation": "cpi_index",
    },
    "en": {
        "inflation": "inflation_rate_yoy", "inflation rate": "inflation_rate_yoy",
        "year-on-year inflation": "inflation_rate_yoy", "year on year inflation": "inflation_rate_yoy",
        "annual inflation": "inflation_rate_yoy", "month-on-month inflation": "inflation_rate_mom",
        "month on month inflation": "inflation_rate_mom", "monthly inflation": "inflation_rate_mom",
        "three-month price change": "inflation_rate_3m", "3-month price change": "inflation_rate_3m",
        "twelve-month average inflation": "inflation_rate_12m_average",
        "12-month average inflation": "inflation_rate_12m_average",
        "cpi": "cpi_index", "consumer price index": "cpi_index",
        "harmonised consumer price index": "cpi_index",
    },
}
_GEOGRAPHIES = {
    "togo": "TG", "tg": "TG", "benin": "BJ", "bj": "BJ", "burkina faso": "BF", "bf": "BF",
    "cote d'ivoire": "CI", "ivory coast": "CI", "ci": "CI", "guinee-bissau": "GW",
    "guinea-bissau": "GW", "gw": "GW", "mali": "ML", "ml": "ML", "niger": "NE", "ne": "NE",
    "senegal": "SN", "sn": "SN", "uemoa": "WAEMU", "waemu": "WAEMU",
}
_MONTHS = {
    name: number for number, names in enumerate((
        "janvier january", "fevrier february", "mars march", "avril april", "mai may", "juin june",
        "juillet july", "aout august", "septembre september", "octobre october", "novembre november",
        "decembre december"), 1) for name in names.split()
}


def _alternatives(values: dict[str, str]) -> str:
    return "(?:" + "|".join(re.escape(value) for value in sorted(values, key=lambda x: (-len(x), x))) + ")"


def _templates(language: str) -> tuple[str, ...]:
    concept = rf"(?P<concept>{_alternatives(_CONCEPTS[language])})"
    basis = r"(?: base (?P<basis>2014|2023))?"
    geography = rf"(?P<geography>{_alternatives(_GEOGRAPHIES)})"
    if language == "fr":
        return (rf"(?:quelle est l'evolution de |montre-moi |montre |affiche )?(?:l'|le |la )?{concept}{basis}"
                rf"(?: (?:au |du |en |de |de la |de l'|dans l'|pour le |pour la ){geography})?"
                r"(?: (?P<period>(?:en |depuis |de |du |entre ).+?))? ?\??",)
    standard = (rf"(?:show me |show |what is the evolution of |what is )?(?:the )?{concept}{basis}"
                rf"(?: (?:in |of |for ){geography})?(?: (?P<period>(?:in |for |from |since |between ).+?))? ?\??")
    evolved = (rf"how has (?:the )?{concept}{basis}(?: in {geography})? evolved"
               r"(?: (?P<period>(?:in |for |from |since |between ).+?))? ?\??")
    return evolved, standard


def _atom(value: str) -> tuple[str, str, int]:
    """Return normalized period, frequency (or year), and chronological ordinal."""
    value = value.lower()
    if re.fullmatch(r"[0-9]{4}", value):
        year = int(value)
        if year:
            return value, "year", year
    match = re.fullmatch(r"([0-9]{4})-([0-9]{2})", value)
    if match:
        year, month = map(int, match.groups())
        if year and 1 <= month <= 12:
            return value, "monthly", (year - 1) * 12 + month - 1
    match = re.fullmatch(r"([a-z]+) ([0-9]{4})", value)
    if match and match[1] in _MONTHS and int(match[2]):
        return _atom(f"{match[2]}-{_MONTHS[match[1]]:02d}")
    match = re.fullmatch(r"(?:([0-9]{4})-q([1-4])|[qt]([1-4]) ([0-9]{4}))", value)
    if match:
        year, quarter = (int(match[1]), int(match[2])) if match[1] else (int(match[4]), int(match[3]))
        if year:
            return f"{year:04d}-Q{quarter}", "quarterly", (year - 1) * 4 + quarter - 1
    raise InterpretationError("invalid_period", "Periode invalide / Invalid period.", ("period",))


def _periods(expression: str, language: str, today: date) -> tuple[str, str, str, tuple[str, ...]]:
    patterns = (("since", r"depuis (.+)" if language == "fr" else r"since (.+)"),
                ("range", r"(?:de |du )(.+?) (?:a|au) (.+)" if language == "fr" else r"from (.+?) to (.+)"),
                ("range", r"entre (.+?) et (.+)" if language == "fr" else r"between (.+?) and (.+)"),
                ("single", r"en (.+)" if language == "fr" else r"(?:in|for) (.+)"))
    for kind, pattern in patterns:
        match = re.fullmatch(pattern, expression)
        if match:
            break
    else:
        raise InterpretationError("unsupported_question", "Precision temporelle non prise en charge / Unsupported period wording.")
    first, frequency, start = _atom(match[1])
    assumptions: list[str] = []
    if kind == "range":
        last, last_frequency, end = _atom(match[2])
        if last_frequency != frequency:
            raise InterpretationError("invalid_period", "Frequences incompatibles / Mixed period frequencies.", ("period",))
    elif kind == "since":
        last = f"{today.year:04d}-Q{(today.month - 1) // 3 + 1}" if frequency == "quarterly" else f"{today.year:04d}-{today.month:02d}"
        _, last_frequency, end = _atom(last)
        if frequency == "year":
            first, frequency, start = _atom(f"{first}-01")
            assumptions.append("year_expanded_to_months")
        assumptions.append("since_ends_at_current_quarter" if frequency == "quarterly" else "since_ends_at_current_month")
    else:
        last, end = first, start
    if frequency == "year":
        first, _, start = _atom(f"{first}-01")
        last, frequency, end = _atom(f"{last}-12")
        assumptions.append("year_expanded_to_months")
    if end < start:
        raise InterpretationError("invalid_period", "Periodes inversees / Reversed period range.", ("period",))
    if end - start + 1 > MAX_PERIODS:
        raise InterpretationError("period_range_too_large", "Maximum 240 periodes / At most 240 periods.", ("period",))
    return first, last, frequency, tuple(assumptions)


def interpret_question(payload: dict[str, object] | QuestionRequest, *, today: date | None = None) -> InterpretResult:
    """Interpret an entire supported question; never consult environment/catalog/data."""
    request = parse_question_request(payload)
    if today is None:
        today = datetime.now(timezone.utc).date()
    if type(today) is not date:
        raise InterpretationError("invalid_question", "Horloge invalide / Invalid trusted clock.")
    text = _normalize(request.question)
    languages = ("fr", "en") if request.language == "auto" else (request.language,)
    for language in languages:
        match = next((result for pattern in _templates(language)
                      if (result := re.fullmatch(pattern, text))), None)
        if match:
            break
    else:
        raise InterpretationError("unsupported_question", "Formulation non prise en charge / Unsupported wording.")
    fields = tuple(name for name in ("geography", "period") if not match[name])
    if fields:
        raise InterpretationError("clarification_required", "Precisez le pays et la periode / Specify geography and period.", fields)
    concept = _CONCEPTS[language][match["concept"]]
    start, end, frequency, temporal_assumptions = _periods(match["period"], language, today)
    if frequency == "quarterly" and concept != "inflation_rate_yoy":
        raise InterpretationError("unsupported_question", "Seul le glissement annuel trimestriel est pris en charge / Only quarterly year-on-year inflation is supported.")
    natural_basis = f"index_{match['basis']}" if match["basis"] else None
    explicit_basis = request.filters.price_basis
    if natural_basis and explicit_basis and natural_basis != explicit_basis:
        raise InterpretationError("invalid_question", "Bases contradictoires / Conflicting index bases.", ("price_basis",))
    basis = natural_basis or explicit_basis
    if concept != "cpi_index" and basis is not None:
        raise InterpretationError("invalid_question", "Une base d'indice ne s'applique pas a un taux / Index basis does not apply to a rate.", ("price_basis",))
    assumptions = ["headline_all_items", *temporal_assumptions]
    if match["concept"] in {"inflation", "taux d'inflation", "inflation rate"}:
        assumptions.append("headline_inflation_yoy")
    return InterpretResult(intent=QueryIntent(selector=SeriesSelector(
        concept=concept, geography=_GEOGRAPHIES[match["geography"]], frequency=frequency,
        unit="index" if concept == "cpi_index" else "percent", category="all_items", sector="all_sectors",
        aggregation_level="total", seasonal_adjustment="not_reported",
        price_basis=basis if concept == "cpi_index" else "not_applicable",
        source_variant=request.filters.source_variant), period_start=start, period_end=end, limit=request.limit),
        language=language, assumptions=tuple(assumptions))
