"""S0.5.1 compiler checks use synthetic metadata and in-memory rows only."""

from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import traceback

from pydantic import ValidationError
import pytest

from sika.query import (
    CatalogEntry,
    MAX_CATALOG_ENTRIES,
    MAX_PERIODS,
    MAX_ROWS,
    QueryError,
    QueryIntent,
    SeriesCatalog,
    SeriesSelector,
    compile_query,
    parse_intent,
    resolve_series,
)


def entry(**updates: object) -> dict[str, object]:
    """Reviewed-shape metadata, explicitly synthetic and not approved data."""
    values: dict[str, object] = {
        "series_key": "test.yoy.tg.monthly.a",
        "concept": "inflation_rate_yoy",
        "geography": "TG",
        "frequency": "monthly",
        "unit": "percent",
        "category": "all_items",
        "sector": "all_sectors",
        "aggregation_level": "total",
        "price_basis": "not_applicable",
        "seasonal_adjustment": "not_reported",
        "source_variant": "test:method_a",
        "concept_aliases": ("annual inflation", "inflation annuelle"),
        "geography_aliases": ("Republic of Togo",),
    }
    values.update(updates)
    return values


def intent(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "selector": {"concept": "annual inflation", "geography": "Togo"},
        "period_start": "2023-01",
        "period_end": "2026-06",
    }
    values.update(updates)
    return values


@pytest.fixture
def catalog() -> SeriesCatalog:
    return SeriesCatalog([entry()])


def assert_error(code: str, callback: Callable[[], object]) -> QueryError:
    with pytest.raises(QueryError) as raised:
        callback()
    assert raised.value.code == code
    return raised.value


def test_contract_defaults_and_explicit_execution_boundary(catalog: SeriesCatalog) -> None:
    parsed = parse_intent(intent())
    plan = compile_query(parsed, catalog)
    assert parsed.version == 1
    assert parsed.operation == "series"
    assert parsed.limit == 100
    assert plan.row_limit == 100
    assert plan.fetch_limit == 101
    assert plan.execution_authorized is False
    assert plan.series == catalog.entries[0]
    assert dict(plan.parameters) == {
        "series_key": "test.yoy.tg.monthly.a",
        "period_start": "2023-01",
        "period_end": "2026-06",
        "fetch_limit": 101,
    }


@pytest.mark.parametrize("value", [True, False, "1", 1.0, None, 0, 2])
def test_version_rejects_coercion_and_unsupported_versions(value: object) -> None:
    assert_error("invalid_intent", lambda: parse_intent(intent(version=value)))


@pytest.mark.parametrize("value", [True, False, "10", 10.0, None, 0, -1, MAX_ROWS + 1])
def test_row_limit_is_a_strict_bounded_integer(value: object) -> None:
    assert_error("invalid_intent", lambda: parse_intent(intent(limit=value)))


@pytest.mark.parametrize("limit", [1, MAX_ROWS])
def test_row_limit_fetches_only_one_extra_sentinel(limit: int, catalog: SeriesCatalog) -> None:
    plan = compile_query(intent(limit=limit), catalog)
    assert plan.row_limit == limit
    assert plan.fetch_limit == limit + 1
    assert plan.parameters["fetch_limit"] == limit + 1


@pytest.mark.parametrize("operation", ["latest", "forecast", "aggregate", "compare", 1])
def test_unsupported_operations_are_rejected(operation: object) -> None:
    assert_error("invalid_intent", lambda: parse_intent(intent(operation=operation)))


@pytest.mark.parametrize("extra", ["sql", "table", "order_by", "conversion", "organization_id"])
def test_request_cannot_add_query_or_authorization_fields(extra: str) -> None:
    payload = intent(**{extra: "UNTRUSTED_SECRET"})
    failure = assert_error("invalid_intent", lambda: parse_intent(payload))
    assert "UNTRUSTED_SECRET" not in str(failure)
    assert "UNTRUSTED_SECRET" not in "".join(traceback.format_exception(failure))


@pytest.mark.parametrize(
    "selector",
    [
        {},
        {"concept": "annual inflation"},
        {"geography": "TG"},
        {"series_key": "test.yoy.tg.monthly.a", "geography": "TG"},
        {"concept": "annual inflation", "geography": "TG", "sql": "SELECT 1"},
        {"concept": 42, "geography": "TG"},
        {"concept": "", "geography": "TG"},
        {"concept": "annual inflation ", "geography": "TG"},
        {"concept": "annual\ninflation", "geography": "TG"},
        {"concept": "annual\x00inflation", "geography": "TG"},
        {"concept": "annual\u200binflation", "geography": "TG"},
        {"concept": "x" * 129, "geography": "TG"},
        {"series_key": "x" * 201},
        {"concept": "annual inflation", "geography": "TG", "category": "all_items;DROP"},
    ],
)
def test_invalid_selectors_are_rejected(selector: dict[str, object], catalog: SeriesCatalog) -> None:
    assert_error("invalid_intent", lambda: resolve_series(selector, catalog))


@pytest.mark.parametrize("payload", [None, [], "SELECT *", 1, True])
def test_non_object_requests_fail_predictably(payload: object) -> None:
    assert_error("invalid_intent", lambda: parse_intent(payload))


def test_aliases_resolve_codes_and_accents(catalog: SeriesCatalog) -> None:
    for concept in ("annual inflation", "Inflation Annuelle", "inflation_rate_yoy", "Inflation en glissement annuel"):
        for geography in ("togo", "TG", "Republic of Togo"):
            result = resolve_series({"concept": concept, "geography": geography}, catalog)
            assert result.series_key == "test.yoy.tg.monthly.a"
    benin = SeriesCatalog([entry(series_key="test.yoy.bj.a", geography="BJ")])
    assert resolve_series({"concept": "annual inflation", "geography": "BENIN"}, benin).geography == "BJ"


def test_unknown_alias_or_key_never_selects_first(catalog: SeriesCatalog) -> None:
    for selector in (
        {"concept": "GDP", "geography": "TG"},
        {"concept": "annual inflation", "geography": "Atlantis"},
        {"series_key": "missing.key"},
    ):
        failure = assert_error("unknown_series", lambda: resolve_series(selector, catalog))
        assert failure.candidate_keys == ()
    assert_error("unknown_series", lambda: resolve_series({"series_key": "missing.key"}, SeriesCatalog([])))


def test_global_alias_does_not_pick_a_single_vintage() -> None:
    first = entry(series_key="test.yoy.tg.monthly.z")
    second = entry(
        series_key="test.yoy.tg.monthly.a", source_variant="test:method_b",
        concept_aliases=(), geography_aliases=(),
    )
    selectors = {"concept": "annual inflation", "geography": "Republic of Togo"}
    for candidates in ([first, second], [second, first]):
        failure = assert_error("ambiguous_series", lambda: resolve_series(selectors, SeriesCatalog(candidates)))
        assert failure.candidate_keys == ("test.yoy.tg.monthly.a", "test.yoy.tg.monthly.z")
    resolved = resolve_series({**selectors, "source_variant": "test:method_b"}, SeriesCatalog([first, second]))
    assert resolved.series_key == "test.yoy.tg.monthly.a"


def test_shared_concept_alias_requires_disambiguation() -> None:
    annual = entry(concept_aliases=("inflation",))
    monthly = entry(series_key="test.mom.tg.a", concept="inflation_rate_mom", concept_aliases=("inflation",))
    values = SeriesCatalog([annual, monthly])
    failure = assert_error("ambiguous_series", lambda: resolve_series({"concept": "inflation", "geography": "TG"}, values))
    assert failure.candidate_keys == ("test.mom.tg.a", "test.yoy.tg.monthly.a")
    assert resolve_series({"concept": "inflation_rate_yoy", "geography": "TG"}, values).concept == "inflation_rate_yoy"


@pytest.mark.parametrize(
    "concept", ["cpi_index", "inflation_rate_yoy", "inflation_rate_mom", "inflation_rate_3m", "inflation_rate_12m_average"],
)
def test_all_reviewed_monthly_concepts_compile(concept: str) -> None:
    index = concept == "cpi_index"
    metadata = entry(
        concept=concept, unit="index" if index else "percent",
        price_basis="index_2023" if index else "not_applicable", concept_aliases=(),
    )
    values = SeriesCatalog([metadata])
    plan = compile_query(intent(selector={"concept": concept, "geography": "TG"}), values)
    assert plan.series.concept == concept
    assert plan.series.unit == ("index" if index else "percent")


@pytest.mark.parametrize(
    "changes, selector",
    [
        ({"category": "food", "aggregation_level": "group"}, {"category": "food"}),
        ({"frequency": "quarterly"}, {"frequency": "quarterly"}),
        ({"source_variant": "test:method_b"}, {"source_variant": "test:method_b"}),
    ],
)
def test_complete_identity_is_never_collapsed(changes: dict[str, object], selector: dict[str, object]) -> None:
    values = SeriesCatalog([entry(), entry(series_key="test.other.identity", **changes)])
    request = {"concept": "annual inflation", "geography": "TG"}
    assert_error("ambiguous_series", lambda: resolve_series(request, values))
    assert resolve_series({**request, **selector}, values).series_key == "test.other.identity"


def test_index_bases_remain_different_series() -> None:
    older = entry(series_key="test.cpi.2014", concept="cpi_index", unit="index", price_basis="index_2014", concept_aliases=())
    newer = {**older, "series_key": "test.cpi.2023", "price_basis": "index_2023"}
    values = SeriesCatalog([newer, older])
    request = {"concept": "cpi_index", "geography": "TG"}
    assert_error("ambiguous_series", lambda: resolve_series(request, values))
    assert resolve_series({**request, "price_basis": "index_2023"}, values).series_key == "test.cpi.2023"


def test_duplicate_keys_and_complete_identities_are_rejected() -> None:
    assert_error("invalid_catalog", lambda: SeriesCatalog([entry(), entry(source_variant="test:method_b")]))
    assert_error("invalid_catalog", lambda: SeriesCatalog([entry(), entry(series_key="test.alias.key")]))


@pytest.mark.parametrize(
    "updates",
    [
        {"concept": "GDP"},
        {"geography": "XX"},
        {"frequency": "annual"},
        {"concept": "inflation_rate_mom", "frequency": "quarterly"},
        {"unit": "index"},
        {"price_basis": "index_2023"},
        {"concept": "cpi_index", "unit": "index", "price_basis": "not_applicable"},
        {"concept": "cpi_index", "unit": "index", "price_basis": "index_2020"},
        {"category": "food", "aggregation_level": "total"},
        {"category": "unknown"},
        {"sector": "industry"},
        {"seasonal_adjustment": "unknown"},
        {"source_variant": ""},
        {"source_variant": "test;DROP"},
        {"concept_aliases": ["annual inflation"]},
        {"concept_aliases": ("x",) * 17},
        {"geography_aliases": ("hidden\x00alias",)},
        {"observation_count": 100},
    ],
)
def test_catalog_metadata_is_strict_and_allowlisted(updates: dict[str, object]) -> None:
    assert_error("invalid_catalog", lambda: SeriesCatalog([entry(**updates)]))


def test_alias_cannot_shadow_another_canonical_code() -> None:
    assert_error("invalid_catalog", lambda: SeriesCatalog([entry(concept_aliases=("inflation_rate_mom",))]))
    assert_error("invalid_catalog", lambda: SeriesCatalog([entry(geography_aliases=("BJ",))]))
    assert_error("invalid_catalog", lambda: SeriesCatalog([entry(geography="BJ", geography_aliases=("Togo",))]))
    assert_error("invalid_catalog", lambda: SeriesCatalog([entry(
        concept="cpi_index", unit="index", price_basis="index_2023",
        concept_aliases=("Inflation en glissement annuel",),
    )]))


def test_catalog_bound_consumes_at_most_one_extra_generator_item() -> None:
    consumed: list[int] = []

    def candidates() -> Iterator[dict[str, object]]:
        for number in range(MAX_CATALOG_ENTRIES + 20):
            consumed.append(number)
            yield entry(series_key=f"test.series.{number}", source_variant=f"test:method_{number}")

    assert_error("invalid_catalog", lambda: SeriesCatalog(candidates()))
    assert len(consumed) == MAX_CATALOG_ENTRIES + 1
    permitted = SeriesCatalog(entry(series_key=f"test.series.{i}", source_variant=f"test:method_{i}") for i in range(MAX_CATALOG_ENTRIES))
    assert len(permitted.entries) == MAX_CATALOG_ENTRIES


def test_frozen_models_and_parameters_cannot_be_mutated(catalog: SeriesCatalog) -> None:
    plan = compile_query(intent(), catalog)
    with pytest.raises(TypeError):
        plan.parameters["fetch_limit"] = 100000
    with pytest.raises(FrozenInstanceError):
        plan.execution_authorized = True
    with pytest.raises(ValidationError):
        catalog.entries[0].source_variant = "test:changed"
    with pytest.raises(FrozenInstanceError):
        catalog.entries = ()


def test_model_construct_and_copy_do_not_bypass_boundary_validation(catalog: SeriesCatalog) -> None:
    constructed = QueryIntent.model_construct(**intent(), limit=True)
    assert_error("invalid_intent", lambda: compile_query(constructed, catalog))
    copied = parse_intent(intent()).model_copy(update={"limit": "500"})
    assert_error("invalid_intent", lambda: compile_query(copied, catalog))
    selector = SeriesSelector.model_construct(series_key="test.yoy.tg.monthly.a", geography="TG")
    assert_error("invalid_intent", lambda: resolve_series(selector, catalog))
    forged = CatalogEntry.model_construct(**entry(frequency="daily"))
    assert_error("invalid_catalog", lambda: SeriesCatalog([forged]))
    forged_catalog = object.__new__(SeriesCatalog)
    object.__setattr__(forged_catalog, "entries", (forged,))
    assert_error("invalid_catalog", lambda: compile_query(intent(), forged_catalog))
    empty_constructed_catalog = object.__new__(SeriesCatalog)
    assert_error("invalid_catalog", lambda: compile_query(intent(), empty_constructed_catalog))


@pytest.mark.parametrize("period", ["0000-01", "2026-00", "2026-13", "2026-Q1", "2026", "2026-1", "202A-01", "２０２６-０１"])
def test_monthly_period_requires_exact_calendar_format(period: str, catalog: SeriesCatalog) -> None:
    expected = "invalid_intent" if len(period) > 7 else "invalid_period"
    assert_error(expected, lambda: compile_query(intent(period_start=period), catalog))


@pytest.mark.parametrize("period", ["0000-Q1", "2026-Q0", "2026-Q5", "2026-01", "2026", "2026-q1"])
def test_quarterly_period_requires_exact_calendar_format(period: str) -> None:
    values = SeriesCatalog([entry(frequency="quarterly")])
    assert_error("invalid_period", lambda: compile_query(intent(period_start=period, period_end="2026-Q4"), values))


def test_reversed_period_range_is_rejected(catalog: SeriesCatalog) -> None:
    assert_error("invalid_period", lambda: compile_query(intent(period_start="2026-06", period_end="2023-01"), catalog))


@pytest.mark.parametrize("start, end", [("2025-Q4", "2026-Q1"), ("2026-Q1", "2026-Q1")])
def test_quarterly_year_transition_and_single_period(start: str, end: str) -> None:
    values = SeriesCatalog([entry(frequency="quarterly")])
    plan = compile_query(intent(period_start=start, period_end=end), values)
    assert plan.parameters["period_start"] == start
    assert plan.parameters["period_end"] == end


@pytest.mark.parametrize(
    "frequency, start, last_valid, first_invalid",
    [("monthly", "2000-01", "2019-12", "2020-01"), ("quarterly", "1960-Q1", "2019-Q4", "2020-Q1")],
)
def test_inclusive_period_bound(frequency: str, start: str, last_valid: str, first_invalid: str) -> None:
    assert MAX_PERIODS == 240
    values = SeriesCatalog([entry(frequency=frequency)])
    compile_query(intent(period_start=start, period_end=last_valid), values)
    assert_error("period_range_too_large", lambda: compile_query(intent(period_start=start, period_end=first_invalid), values))


def test_sql_is_fixed_and_all_request_values_are_bound(catalog: SeriesCatalog) -> None:
    first = compile_query(intent(), catalog)
    second = compile_query(intent(period_start="2024-01", period_end="2024-12", limit=5), catalog)
    assert first.sql == second.sql
    assert "FROM authorized_observations" in first.sql
    assert "FROM observations" not in first.sql
    assert "ORDER BY period ASC, release_id ASC, observation_id ASC" in first.sql
    assert "LIMIT :fetch_limit" in first.sql
    assert set(first.parameters) == {"series_key", "period_start", "period_end", "fetch_limit"}
    assert all(str(value) not in first.sql for value in first.parameters.values())
    assert ";" not in first.sql
    assert "published" not in first.sql


@pytest.fixture
def authorized_rows() -> Iterator[sqlite3.Connection]:
    """The adapter-shaped relation exists only in this synthetic memory database."""
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE authorized_observations (
        observation_id INTEGER PRIMARY KEY, series_key TEXT NOT NULL,
        period TEXT NOT NULL, value REAL NOT NULL, unit TEXT NOT NULL,
        release_id INTEGER NOT NULL, source_doc TEXT NOT NULL,
        source_page INTEGER NOT NULL, source_url TEXT NOT NULL,
        source_locator TEXT, revision_status TEXT NOT NULL
    )""")
    rows = [
        (6, "test.yoy.tg.monthly.a", "2026-06", 6.0, "percent", 2, "FIXTURE_RELEASE_B.pdf", 8, "https://example.invalid/b", "table B", "revised"),
        (3, "test.yoy.tg.monthly.a", "2024-01", 3.0, "percent", 1, "FIXTURE_RELEASE_A.pdf", 4, "https://example.invalid/a", "table A", "provisional"),
        (1, "test.yoy.tg.monthly.a", "2022-12", 1.0, "percent", 1, "FIXTURE_RELEASE_A.pdf", 2, "https://example.invalid/a", "table A", "unknown"),
        (4, "test.yoy.tg.monthly.a", "2024-01", 4.0, "percent", 2, "FIXTURE_RELEASE_B.pdf", 5, "https://example.invalid/b", "table B", "revised"),
        (7, "test.other.series", "2024-01", 7.0, "percent", 1, "FIXTURE_OTHER.pdf", 2, "https://example.invalid/other", None, "final"),
        (5, "test.yoy.tg.monthly.a", "2025-01", 5.0, "percent", 2, "FIXTURE_RELEASE_B.pdf", 6, "https://example.invalid/b", "table B", "final"),
        (2, "test.yoy.tg.monthly.a", "2023-01", 2.0, "percent", 1, "FIXTURE_RELEASE_A.pdf", 3, "https://example.invalid/a", "table A", "unknown"),
    ]
    con.executemany("INSERT INTO authorized_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    yield con
    con.close()


def test_structured_gq1_equivalent_preserves_periods_vintages_and_citations(
    catalog: SeriesCatalog, authorized_rows: sqlite3.Connection,
) -> None:
    plan = compile_query(intent(), catalog)
    rows = [dict(row) for row in authorized_rows.execute(plan.sql, dict(plan.parameters))]
    assert [row["observation_id"] for row in rows] == [2, 3, 4, 5, 6]
    assert [row["period"] for row in rows] == ["2023-01", "2024-01", "2024-01", "2025-01", "2026-06"]
    assert [row["value"] for row in rows] == [2.0, 3.0, 4.0, 5.0, 6.0]
    assert rows[1]["release_id"] == 1 and rows[2]["release_id"] == 2
    for row in rows:
        assert row["source_doc"].startswith("FIXTURE_")
        assert row["source_page"] >= 1
        assert row["source_url"].startswith("https://example.invalid/")
        assert row["source_locator"] in {"table A", "table B"}
        assert row["revision_status"] in {"unknown", "provisional", "revised", "final"}


def test_in_memory_execution_returns_sentinel_for_truncation(
    catalog: SeriesCatalog, authorized_rows: sqlite3.Connection,
) -> None:
    plan = compile_query(intent(limit=3), catalog)
    rows = authorized_rows.execute(plan.sql, dict(plan.parameters)).fetchall()
    assert len(rows) == plan.row_limit + 1
    assert [row["observation_id"] for row in rows[:plan.row_limit]] == [2, 3, 4]
    assert rows[-1]["observation_id"] == 5
    assert plan.execution_authorized is False


def test_future_period_returns_empty_not_forecast(
    catalog: SeriesCatalog, authorized_rows: sqlite3.Connection,
) -> None:
    plan = compile_query(intent(period_start="2030-01", period_end="2030-12"), catalog)
    assert authorized_rows.execute(plan.sql, dict(plan.parameters)).fetchall() == []


def test_exact_key_with_sql_punctuation_is_bound_not_executed(authorized_rows: sqlite3.Connection) -> None:
    malicious_key = "test.key'; DROP TABLE authorized_observations; --"
    values = SeriesCatalog([entry(series_key=malicious_key)])
    plan = compile_query(intent(selector={"series_key": malicious_key}), values)
    assert malicious_key not in plan.sql
    assert plan.parameters["series_key"] == malicious_key
    assert authorized_rows.execute(plan.sql, dict(plan.parameters)).fetchall() == []
    assert authorized_rows.execute("SELECT COUNT(*) FROM authorized_observations").fetchone()[0] == 7


def test_injection_alias_fails_before_execution(
    catalog: SeriesCatalog, authorized_rows: sqlite3.Connection,
) -> None:
    malicious_alias = "annual inflation'; DROP TABLE authorized_observations; --"
    failure = assert_error("unknown_series", lambda: compile_query(intent(selector={"concept": malicious_alias, "geography": "TG"}), catalog))
    assert malicious_alias not in str(failure)
    assert authorized_rows.execute("SELECT COUNT(*) FROM authorized_observations").fetchone()[0] == 7


def test_same_intent_and_catalog_order_create_identical_plan() -> None:
    first = entry()
    second = entry(series_key="test.yoy.bj.a", geography="BJ")
    left = compile_query(intent(), SeriesCatalog([first, second]))
    right = compile_query(intent(), SeriesCatalog([second, first]))
    assert left.sql == right.sql
    assert left.parameters == right.parameters
    assert left.series == right.series


def test_import_and_compile_need_no_db_env_provider_or_file_write(tmp_path: Path) -> None:
    code = """
import pathlib
import socket
import sqlite3
import dotenv
import openai

def forbidden(*args, **kwargs):
    raise AssertionError('Compiler tried to perform I/O or create a provider client')

sqlite3.connect = forbidden
socket.create_connection = forbidden
dotenv.load_dotenv = forbidden
openai.OpenAI = forbidden
openai.AsyncOpenAI = forbidden
pathlib.Path.open = forbidden
pathlib.Path.write_bytes = forbidden
pathlib.Path.write_text = forbidden
from sika.query import SeriesCatalog, compile_query
catalog = SeriesCatalog([ENTRY])
plan = compile_query(INTENT, catalog)
assert not plan.execution_authorized
""".replace("ENTRY", repr(entry())).replace("INTENT", repr(intent()))
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.pop("OPENAI_API_KEY", None)
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
