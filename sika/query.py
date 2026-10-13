"""Prepare bounded inflation queries without reading or authorizing any data.

S0.5.1 is a pure contract/compiler. ``authorized_observations`` is a required
future adapter relation, NOT an existing view or a substitute for fresh
``publication.published_rows`` proof. No execution adapter is supplied here.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import re
from types import MappingProxyType
from typing import Annotated, Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from sika.inflation_mapping import CATEGORIES, CONCEPT_LABELS, GEOGRAPHIES, normalize

MAX_ROWS = 500
MAX_PERIODS = 240
MAX_CATALOG_ENTRIES = 2000
DIMENSIONS = (
    "category", "sector", "aggregation_level", "price_basis",
    "seasonal_adjustment", "source_variant",
)
IDENTITY_FIELDS = ("concept", "geography", "frequency", "unit", *DIMENSIONS)
_CATEGORY_LEVELS = {code: level for code, level, _ in CATEGORIES}
_GEOGRAPHY_LABELS = {code: label for code, label in GEOGRAPHIES.values()}
_CODE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}\Z", re.ASCII)

Text = Annotated[str, Field(min_length=1, max_length=128)]
SeriesKey = Annotated[str, Field(min_length=1, max_length=200)]
Frequency = Literal["monthly", "quarterly"]
Unit = Literal["index", "percent"]


class QueryError(ValueError):
    """Stable failure code; never echo untrusted request text in the message."""

    def __init__(self, code: str, message: str, candidate_keys: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.candidate_keys = candidate_keys


def _clean_text(value: str) -> str:
    if value != value.strip() or any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("Text must be trimmed and contain no control characters")
    if not normalize(value):
        raise ValueError("Text must contain a supported alias")
    return value


def _code(value: str) -> str:
    if not _CODE.fullmatch(value):
        raise ValueError("Expected a canonical code")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")


class CatalogEntry(_StrictModel):
    """Server-owned identity metadata, never publication or numeric authority."""

    series_key: SeriesKey
    concept: Text
    geography: Text
    frequency: Frequency
    unit: Unit
    category: Text
    sector: Literal["all_sectors"]
    aggregation_level: Text
    price_basis: Text
    seasonal_adjustment: Literal["not_reported"]
    source_variant: Text
    concept_aliases: Annotated[tuple[Text, ...], Field(max_length=16)] = ()
    geography_aliases: Annotated[tuple[Text, ...], Field(max_length=16)] = ()

    @field_validator("series_key")
    @classmethod
    def clean_key(cls, value: str) -> str:
        return _clean_text(value)

    @field_validator("concept", "geography", *DIMENSIONS)
    @classmethod
    def valid_code(cls, value: str) -> str:
        return _code(value)

    @field_validator("concept_aliases", "geography_aliases")
    @classmethod
    def clean_aliases(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_clean_text(value) for value in values)

    @model_validator(mode="after")
    def pilot_identity(self) -> "CatalogEntry":
        if self.concept not in CONCEPT_LABELS or self.geography not in _GEOGRAPHY_LABELS:
            raise ValueError("Concept or geography is outside the inflation pilot")
        if _CATEGORY_LEVELS.get(self.category) != self.aggregation_level:
            raise ValueError("Category and aggregation level must match the reviewed taxonomy")
        if self.concept == "cpi_index":
            if self.unit != "index" or self.price_basis not in {"index_2014", "index_2023"}:
                raise ValueError("CPI requires its explicit index basis")
        elif self.unit != "percent" or self.price_basis != "not_applicable":
            raise ValueError("Inflation rates require percent and no index basis")
        if self.frequency == "quarterly" and self.concept != "inflation_rate_yoy":
            raise ValueError("Only quarterly year-on-year inflation is in the reviewed pilot")
        return self

    @property
    def identity(self) -> tuple[str, ...]:
        return tuple(getattr(self, name) for name in IDENTITY_FIELDS)


@dataclass(frozen=True, init=False)
class SeriesCatalog:
    """Bounded validated metadata. Alias collisions are retained, not overwritten."""

    entries: tuple[CatalogEntry, ...]

    def __init__(self, entries: Iterable[CatalogEntry | dict[str, object]]) -> None:
        validated: list[CatalogEntry] = []
        keys: set[str] = set()
        identities: set[tuple[str, ...]] = set()
        try:
            for entry in entries:
                if len(validated) >= MAX_CATALOG_ENTRIES:
                    raise ValueError("Catalog exceeds its bound")
                item = CatalogEntry.model_validate(entry)
                if item.series_key in keys or item.identity in identities:
                    raise ValueError("Duplicate series key or complete identity")
                keys.add(item.series_key)
                identities.add(item.identity)
                validated.append(item)
            _alias_index(tuple(validated), "concept")
            _alias_index(tuple(validated), "geography")
        except (ValidationError, ValueError, TypeError):
            raise QueryError("invalid_catalog", "The server catalog is invalid.") from None
        object.__setattr__(self, "entries", tuple(sorted(validated, key=lambda item: item.series_key)))


class SeriesSelector(_StrictModel):
    """Exact key OR descriptive filters, with no implicit dimension defaults."""

    series_key: SeriesKey | None = None
    concept: Text | None = None
    geography: Text | None = None
    frequency: Frequency | None = None
    unit: Unit | None = None
    category: Text | None = None
    sector: Text | None = None
    aggregation_level: Text | None = None
    price_basis: Text | None = None
    seasonal_adjustment: Text | None = None
    source_variant: Text | None = None

    @field_validator("series_key", "concept", "geography")
    @classmethod
    def clean_alias(cls, value: str | None) -> str | None:
        return _clean_text(value) if value is not None else None

    @field_validator(*DIMENSIONS)
    @classmethod
    def valid_code(cls, value: str | None) -> str | None:
        return _code(value) if value is not None else None

    @model_validator(mode="after")
    def exclusive_key(self) -> "SeriesSelector":
        if self.series_key is not None:
            if any(getattr(self, name) is not None for name in IDENTITY_FIELDS):
                raise ValueError("An exact key cannot be combined with descriptive filters")
        elif self.concept is None or self.geography is None:
            raise ValueError("Concept and geography are required without an exact key")
        return self


class QueryIntent(_StrictModel):
    """Version 1 only retrieves an explicit inclusive series period range."""

    version: Literal[1] = 1
    operation: Literal["series"] = "series"
    selector: SeriesSelector
    period_start: Annotated[str, Field(min_length=4, max_length=7)]
    period_end: Annotated[str, Field(min_length=4, max_length=7)]
    limit: Annotated[int, Field(ge=1, le=MAX_ROWS)] = 100

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("Expected integer contract version 1")
        return value


@dataclass(frozen=True)
class PreparedQuery:
    """A plan, not an access grant. The extra fetched row is an internal sentinel."""

    sql: str
    parameters: Mapping[str, object]
    series: CatalogEntry
    row_limit: int
    fetch_limit: int
    execution_authorized: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


def _alias_index(entries: tuple[CatalogEntry, ...], field_name: str) -> dict[str, set[str]]:
    # Aliases resolve a concept/geography code globally, never just the entry on
    # which an alias was declared. Missing aliases must not select a vintage.
    labels = CONCEPT_LABELS if field_name == "concept" else _GEOGRAPHY_LABELS
    reserved = {normalize(name): code for code, label in labels.items() for name in (code, label)}
    index: dict[str, set[str]] = {}
    for entry in entries:
        code = getattr(entry, field_name)
        for alias in (code, labels[code], *getattr(entry, f"{field_name}_aliases")):
            normalized = normalize(alias)
            if normalized in reserved and reserved[normalized] != code:
                raise ValueError("An alias shadows another canonical code or official label")
            index.setdefault(normalized, set()).add(code)
    return index


def parse_intent(payload: dict[str, object] | QueryIntent) -> QueryIntent:
    """Revalidate even previously constructed models; no coercion or raw SQL."""
    try:
        return QueryIntent.model_validate(payload)
    except (ValidationError, ValueError, TypeError):
        raise QueryError("invalid_intent", "The query intent does not match contract version 1.") from None


def resolve_series(selector: dict[str, object] | SeriesSelector, catalog: SeriesCatalog) -> CatalogEntry:
    """Require one complete identity; candidates in errors have stable ordering."""
    try:
        selected = SeriesSelector.model_validate(selector)
    except (ValidationError, ValueError, TypeError):
        raise QueryError("invalid_intent", "The series selector is invalid.") from None
    if not isinstance(catalog, SeriesCatalog):
        raise QueryError("invalid_catalog", "The server catalog is invalid.")
    # Frozen/constructed objects alone are not validation evidence at a boundary.
    try:
        checked = SeriesCatalog(catalog.entries)
    except AttributeError:
        raise QueryError("invalid_catalog", "The server catalog is invalid.") from None
    if selected.series_key is not None:
        candidates = [item for item in checked.entries if item.series_key == selected.series_key]
    else:
        concept_codes = _alias_index(checked.entries, "concept").get(normalize(selected.concept), set())
        geography_codes = _alias_index(checked.entries, "geography").get(normalize(selected.geography), set())
        candidates = [
            item for item in checked.entries
            if item.concept in concept_codes and item.geography in geography_codes
            and all(getattr(selected, name) is None or getattr(selected, name) == getattr(item, name)
                    for name in ("frequency", "unit", *DIMENSIONS))
        ]
    if not candidates:
        raise QueryError("unknown_series", "No canonical series matches the selector.")
    if len(candidates) > 1:
        raise QueryError("ambiguous_series", "Specify the remaining series dimensions.",
                         tuple(item.series_key for item in candidates))
    return candidates[0]


def _period_ordinal(value: str, frequency: Frequency) -> int:
    pattern = r"([0-9]{4})-(0[1-9]|1[0-2])" if frequency == "monthly" else r"([0-9]{4})-Q([1-4])"
    match = re.fullmatch(pattern, value, flags=re.ASCII)
    if match is None or match[1] == "0000":
        raise QueryError("invalid_period", "Periods must match the resolved series frequency and calendar.")
    per_year = 12 if frequency == "monthly" else 4
    return (int(match[1]) - 1) * per_year + int(match[2]) - 1


_SERIES_SQL = """SELECT observation_id, series_key, period, value, unit,
       release_id, source_doc, source_page, source_url, source_locator, revision_status
FROM authorized_observations
WHERE series_key = :series_key
  AND period >= :period_start AND period <= :period_end
ORDER BY period ASC, release_id ASC, observation_id ASC
LIMIT :fetch_limit"""


def compile_query(payload: dict[str, object] | QueryIntent, catalog: SeriesCatalog) -> PreparedQuery:
    """Compile fixed SQL with bound values only; never open a DB or call a model.

    A future adapter must obtain fresh publication proof, remove the limit+1
    sentinel, and disclose truncation before rendering/exporting a result.
    """
    intent = parse_intent(payload)
    series = resolve_series(intent.selector, catalog)
    start = _period_ordinal(intent.period_start, series.frequency)
    end = _period_ordinal(intent.period_end, series.frequency)
    if end < start:
        raise QueryError("invalid_period", "The end period precedes the start period.")
    if end - start + 1 > MAX_PERIODS:
        raise QueryError("period_range_too_large", "The inclusive range exceeds 240 periods.")
    return PreparedQuery(
        sql=_SERIES_SQL,
        parameters={"series_key": series.series_key, "period_start": intent.period_start,
                    "period_end": intent.period_end, "fetch_limit": intent.limit + 1},
        series=series, row_limit=intent.limit, fetch_limit=intent.limit + 1,
    )
