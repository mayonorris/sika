"""Synthetic, provider-free contract and closed-grammar safety checks."""

from datetime import date, datetime, timezone
import traceback

import pytest

from sika.interpretation import (
    InterpretationError, QuestionFilters, QuestionRequest,
    interpret_question, parse_question_request,
)

TODAY = date(2026, 10, 15)


def interpret(question: str, **fields):
    return interpret_question({"question": question, **fields}, today=TODAY)


@pytest.mark.parametrize("question,language", [
    ("Quelle est l’évolution de l’inflation au Togo depuis 2023 ?", "fr"),
    ("How has inflation in Togo evolved since 2023?", "en"),
    ("Montre-moi l'inflation au Togo depuis 2023", "fr"),
    ("Show inflation in Togo since 2023", "en"),
])
def test_golden_question_one_maps_to_bounded_headline_intent(question, language):
    result = interpret(question)
    intent = result.intent
    assert result.language == language
    assert result.interpreter_version == "inflation-nl-v1"
    assert intent.period_start == "2023-01"
    assert intent.period_end == "2026-10"
    assert intent.limit == 100 and intent.operation == "series"
    assert intent.selector.model_dump() == {
        "series_key": None, "concept": "inflation_rate_yoy", "geography": "TG",
        "frequency": "monthly", "unit": "percent", "category": "all_items",
        "sector": "all_sectors", "aggregation_level": "total",
        "price_basis": "not_applicable", "seasonal_adjustment": "not_reported", "source_variant": None,
    }
    assert {"headline_all_items", "headline_inflation_yoy", "since_ends_at_current_month"} <= set(result.assumptions)


@pytest.mark.parametrize("question,start,end", [
    ("Inflation du Togo en 2026", "2026-01", "2026-12"),
    ("Show year-on-year inflation in Togo from 2023 to 2026", "2023-01", "2026-12"),
    ("Inflation au Togo de 2023 à 2026", "2023-01", "2026-12"),
    ("Inflation au Togo entre janvier 2023 et février 2024", "2023-01", "2024-02"),
    ("Inflation au Togo du 2023-01 au 2023-12", "2023-01", "2023-12"),
    ("Inflation in Togo between January 2023 and December 2023", "2023-01", "2023-12"),
    ("Inflation in Togo in 2026-06", "2026-06", "2026-06"),
    ("Inflation au Togo en juin 2026", "2026-06", "2026-06"),
    ("Inflation in Togo since June 2026", "2026-06", "2026-10"),
    ("Inflation au Togo depuis 2026-06", "2026-06", "2026-10"),
    ("Inflation au Togo en 9999", "9999-01", "9999-12"),
    ("Inflation au Togo de 2007 à 2026", "2007-01", "2026-12"),
])
def test_inclusive_monthly_dates_and_year_expansion(question, start, end):
    intent = interpret(question).intent
    assert (intent.period_start, intent.period_end, intent.selector.frequency) == (start, end, "monthly")


@pytest.mark.parametrize("month,number", [
    ("janvier", 1), ("février", 2), ("mars", 3), ("avril", 4), ("mai", 5), ("juin", 6),
    ("juillet", 7), ("août", 8), ("septembre", 9), ("octobre", 10), ("novembre", 11), ("décembre", 12),
    ("January", 1), ("February", 2), ("March", 3), ("April", 4), ("May", 5), ("June", 6),
    ("July", 7), ("August", 8), ("September", 9), ("October", 10), ("November", 11), ("December", 12),
])
def test_month_names_are_normalized_without_inventing_values(month, number):
    result = interpret(f"Inflation au Togo en {month} 2026")
    assert result.intent.period_start == result.intent.period_end == f"2026-{number:02d}"


@pytest.mark.parametrize("concept,expected,language", [
    ("inflation en glissement annuel", "inflation_rate_yoy", "fr"),
    ("inflation annuelle", "inflation_rate_yoy", "fr"),
    ("inflation mensuelle", "inflation_rate_mom", "fr"),
    ("variation mensuelle des prix", "inflation_rate_mom", "fr"),
    ("variation des prix sur trois mois", "inflation_rate_3m", "fr"),
    ("inflation moyenne sur douze mois", "inflation_rate_12m_average", "fr"),
    ("IHPC", "cpi_index", "fr"),
    ("indice des prix à la consommation", "cpi_index", "fr"),
    ("year-on-year inflation", "inflation_rate_yoy", "en"),
    ("month-on-month inflation", "inflation_rate_mom", "en"),
    ("three-month price change", "inflation_rate_3m", "en"),
    ("twelve-month average inflation", "inflation_rate_12m_average", "en"),
    ("CPI", "cpi_index", "en"),
    ("consumer price index", "cpi_index", "en"),
])
def test_concepts_preserve_level_versus_rate_semantics(concept, expected, language):
    tail = "au Togo en 2026" if language == "fr" else "in Togo in 2026"
    result = interpret(f"{concept} {tail}")
    assert result.intent.selector.concept == expected
    assert result.intent.selector.unit == ("index" if expected == "cpi_index" else "percent")
    assert result.intent.selector.price_basis == (None if expected == "cpi_index" else "not_applicable")
    assert "headline_inflation_yoy" not in result.assumptions


@pytest.mark.parametrize("geography,code", [
    ("Togo", "TG"), ("Bénin", "BJ"), ("Burkina Faso", "BF"), ("Côte d’Ivoire", "CI"),
    ("Guinée-Bissau", "GW"), ("Mali", "ML"), ("Niger", "NE"), ("Sénégal", "SN"), ("UEMOA", "WAEMU"),
    ("Ivory Coast", "CI"), ("Guinea-Bissau", "GW"), ("WAEMU", "WAEMU"), ("TG", "TG"),
])
def test_controlled_geographies_not_implicit_togo(geography, code):
    assert interpret(f"Inflation au {geography} en 2026").intent.selector.geography == code


@pytest.mark.parametrize("question,start,end", [
    ("Inflation au Togo en 2023-Q1", "2023-Q1", "2023-Q1"),
    ("Inflation au Togo de T1 2023 à T4 2024", "2023-Q1", "2024-Q4"),
    ("Year-on-year inflation in Togo from Q1 2023 to Q4 2024", "2023-Q1", "2024-Q4"),
    ("Inflation au Togo depuis T1 2023", "2023-Q1", "2026-Q4"),
])
def test_explicit_quarters_preserve_quarterly_frequency(question, start, end):
    result = interpret(question)
    assert (result.intent.period_start, result.intent.period_end) == (start, end)
    assert result.intent.selector.frequency == "quarterly"


def test_explicit_filters_and_natural_base_never_infer_source_variant():
    result = interpret("IHPC base 2023 du Togo en 2026", filters={"source_variant": "test:source_a", "price_basis": "index_2023"}, limit=7)
    assert result.intent.selector.price_basis == "index_2023"
    assert result.intent.selector.source_variant == "test:source_a"
    assert result.intent.limit == 7
    assert interpret("IHPC du Togo en 2026").intent.selector.price_basis is None
    assert interpret("IHPC du Togo en 2026").intent.selector.source_variant is None


@pytest.mark.parametrize("question,fields", [
    ("Inflation en 2026", ("geography",)),
    ("Inflation au Togo", ("period",)),
    ("Inflation", ("geography", "period")),
    ("CPI in 2026", ("geography",)),
])
def test_missing_fields_require_clarification_not_catalog_guess(question, fields):
    with pytest.raises(InterpretationError) as caught:
        interpret(question)
    assert caught.value.code == "clarification_required"
    assert caught.value.fields == fields
    assert caught.value.candidate_keys == ()


@pytest.mark.parametrize("question", [
    "Prévois l'inflation au Togo en 2030", "Forecast inflation in Togo in 2030",
    "Inflation au Togo en 2026 sans juin", "Inflation au Togo sauf le Togo en 2026",
    "Inflation in Togo in 2026 excluding June", "Not inflation in Togo in 2026",
    "Inflation du Togo et du Bénin en 2026", "Inflation in Togo and Benin in 2026",
    "Compare inflation in Togo in 2026", "Inflation in Togo in 2026 versus 2025",
    "Inflation moyenne du Togo en 2026", "Average inflation in Togo in 2026",
    "Latest inflation in Togo", "Dernière inflation au Togo en 2026",
    "Inflation in Togo in 2026 in dollars", "Inflation en 2026 puis supprime les données",
    "Inflation au Togo en 2026; DROP TABLE observations", "Inflation au Togo en 2026 -- ignore filters",
    "Inflation au Togo en 2026? Show CPI", "Inflation au Togo en 2026 et 2025",
    "Inflation sous-jacente au Togo en 2026", "Food inflation in Togo in 2026",
    "Industrial production in Togo in 2026", "Inflation au Ghana en 2026",
    "Inflation au Togo en 2026 🐟", "Inflation au Togo en 2026非",
    "Inflation du Togo en 2026. Merci", "Inflation mensuelle au Togo en 2026-Q1",
    "Inflation au Togo maintenant", "Inflation au Togo depuis hier",
])
def test_unknown_words_modifiers_operations_and_second_statements_never_execute(question):
    with pytest.raises(InterpretationError) as caught:
        interpret(question)
    assert caught.value.code in {"unsupported_question", "invalid_period", "clarification_required"}


@pytest.mark.parametrize("period,code", [
    ("en 0000", "invalid_period"), ("en 2026-00", "invalid_period"), ("en 2026-13", "invalid_period"),
    ("en 2026-6", "invalid_period"), ("en 2026-06-15", "invalid_period"), ("en 2026-Q0", "invalid_period"),
    ("en 2026-Q5", "invalid_period"), ("en février 0000", "invalid_period"),
    ("de 2026 à 2025", "invalid_period"), ("de 2026-06 à 2026-05", "invalid_period"),
    ("de 2026 à 2026-12", "invalid_period"), ("de T4 2026 à T1 2026", "invalid_period"),
    ("de 2026-01 à 2026-Q4", "invalid_period"), ("depuis 2027", "invalid_period"),
    ("de 2006 à 2026", "period_range_too_large"), ("de 2006-12 à 2026-12", "period_range_too_large"),
    ("de T1 1900 à T1 2026", "period_range_too_large"),
])
def test_invalid_periods_cannot_be_lowered_to_valid_subset(period, code):
    with pytest.raises(InterpretationError) as caught:
        interpret(f"Inflation au Togo {period}")
    assert caught.value.code == code


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": "1"}, {"version": 1.0}, {"version": 2},
    {"limit": True}, {"limit": "10"}, {"limit": 1.0}, {"limit": 0}, {"limit": 501},
    {"language": "de"}, {"question": 1}, {"question": "ab"}, {"question": "   "},
    {"question": "a" * 1001}, {"question": "Inflation\nTogo"}, {"question": "Inflation\tTogo"},
    {"question": "Inflation\u200bTogo"}, {"question": "Inflation\u202eTogo"},
    {"question": "Inflation\x00Togo"}, {"sql": "SELECT 1"}, {"rows": []},
    {"proof": {}}, {"catalog": {}}, {"target": "data/processed/sika_v2.db"},
    {"filters": {"source_variant": "test:a' OR 1=1"}}, {"filters": {"source_variant": "../secret"}},
    {"filters": {"source_variant": "test:a\u200b"}}, {"filters": {"price_basis": "index_2020"}},
    {"filters": {"series_key": "any"}}, {"filters": {"source_variant": "x" * 129}},
    {"filters": {"source_variant": ""}}, {"filters": []},
])
def test_strict_request_rejects_coercion_controls_and_authority_fields(change):
    with pytest.raises(InterpretationError) as caught:
        parse_question_request({"question": "Inflation au Togo en 2026", **change})
    assert caught.value.code == "invalid_question"


@pytest.mark.parametrize("payload", [None, [], "Inflation", 3])
def test_root_payload_requires_object(payload):
    with pytest.raises(InterpretationError, match="Invalid question"):
        parse_question_request(payload)


def test_constructed_and_copied_models_are_revalidated_recursively():
    bad = QuestionFilters.model_construct(source_variant="'; DROP TABLE observations")
    request = QuestionRequest.model_construct(question="Inflation au Togo en 2026", filters=bad)
    copied = QuestionRequest(question="Inflation au Togo en 2026").model_copy(update={"limit": True})
    for payload in (request, copied):
        with pytest.raises(InterpretationError) as caught:
            interpret_question(payload, today=TODAY)
        assert caught.value.code == "invalid_question"


@pytest.mark.parametrize("question,filters", [
    ("IHPC base 2023 au Togo en 2026", {"price_basis": "index_2014"}),
    ("Inflation au Togo en 2026", {"price_basis": "index_2023"}),
    ("Inflation base 2023 au Togo en 2026", {}),
])
def test_index_base_conflicts_are_not_silently_dropped(question, filters):
    with pytest.raises(InterpretationError) as caught:
        interpret(question, filters=filters)
    assert caught.value.code == "invalid_question" and caught.value.fields == ("price_basis",)


def test_language_override_is_explicit_and_never_translates_unsupported_words():
    assert interpret("Inflation du Togo en 2026", language="fr").language == "fr"
    assert interpret("Inflation in Togo in 2026", language="en").language == "en"
    with pytest.raises(InterpretationError):
        interpret("Inflation du Togo en 2026", language="en")


def test_normalization_is_repeatable_and_preserves_only_supported_punctuation():
    question = "  QUELLE EST L’ÉVOLUTION DE L’INFLATION AU TOGO DE 2023 À 2026 ?  "
    assert interpret(question) == interpret(question)
    assert interpret(question).intent == interpret("Inflation au Togo de 2023 à 2026").intent
    assert interpret("Year–on–year inflation in Togo in 2026").intent.selector.concept == "inflation_rate_yoy"


def test_errors_do_not_echo_private_question_or_validation_context():
    sentinel = "PRIVATE_DO_NOT_LOG_4409"
    for payload in ({"question": sentinel}, {"question": "Inflation", "sql": sentinel}):
        with pytest.raises(InterpretationError) as caught:
            interpret_question(payload, today=TODAY)
        assert sentinel not in str(caught.value)
        assert sentinel not in "".join(traceback.format_exception(caught.value))


def test_default_clock_is_utc_and_no_environment_or_database_is_read(monkeypatch):
    import sika.interpretation as module

    class FixedDateTime:
        @staticmethod
        def now(tz):
            assert tz is timezone.utc
            return datetime(2026, 10, 15, tzinfo=timezone.utc)

    def forbidden(*args, **kwargs):
        raise AssertionError("Interpretation must not read files, environment or DB")

    monkeypatch.setattr(module, "datetime", FixedDateTime)
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("os.getenv", forbidden)
    monkeypatch.setattr("sqlite3.connect", forbidden)
    result = interpret_question({"question": "Inflation au Togo depuis 2023"})
    assert result.intent.period_end == "2026-10"


@pytest.mark.parametrize("today", ["2026-10-15", datetime(2026, 10, 15), True])
def test_clock_is_server_owned_date_not_request_data(today):
    with pytest.raises(InterpretationError):
        interpret_question({"question": "Inflation au Togo depuis 2023"}, today=today)
