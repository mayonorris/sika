"""Deterministic IHPC table projection, independent of the local source archive."""

from pathlib import Path

import pytest

from sika import source_tables as tables

DOC = "inseed_ihpc_2026-05.pdf"
DIGEST = tables.SUPPORTED_HASHES[DOC]
HEADER = ["Libellé", "Pondération", "mai-25", "févr-26", "mars-26", "avr-26",
          "mai-26", "1mois", "3mois", "12mois"]
ROW = ["Global", "10000", "104,6", "103,6", "104,5", "104,6", "105,0", "0,4", "1,3", "0,4"]


def _parse(header=None, rows=None, *, countries=False, month_row=0):
    data = [header or list(HEADER)] + (rows or [list(ROW)])
    spec = tables._TableSpec(5, 1, 3, 0, month_row, len(data), countries)
    return tables._parse_grid(DOC, DIGEST, spec, data)


def test_headers_determine_period_not_document_name():
    header = ["Libellé", "Pondération", "juin-25", "mars-26", "avr-26", "mai-26",
              "juin-26", "1mois", "3mois", "12mois"]
    facts = _parse(header)
    assert {f["period"] for f in facts if f["concept"] != "cpi_index"} == {"2026-06"}
    assert facts[-1]["value"] == "0.4"


def test_blank_grid_column_does_not_shift_numeric_cells():
    header, row = list(HEADER), list(ROW)
    header.insert(6, "")
    row.insert(6, "")
    facts = _parse(header, [row])
    assert len(facts) == 8
    current = next(f for f in facts if f["concept"] == "cpi_index" and f["period"] == "2026-05")
    assert current["value"] == "105.0"


def test_spanning_current_month_header_projects_in_printed_order():
    # Actual group-table layout has an empty header column before the final
    # month, but an empty body column after its value, due to a merged cell.
    header, row = list(HEADER), list(ROW)
    header.insert(6, "")
    row.insert(7, None)
    facts = _parse(header, [row])
    assert facts[4]["period"] == "2026-05"
    assert facts[4]["value"] == "105.0"
    assert [f["value"] for f in facts[-3:]] == ["0.4", "1.3", "0.4"]


def test_split_decimal_across_spurious_grid_boundary_is_not_two_values():
    header, row = list(HEADER), list(ROW)
    header.insert(4, None)
    row[3] = "103,"
    row.insert(4, "6")
    facts = _parse(header, [row])
    assert facts[1]["value"] == "103.6"
    assert len(facts) == 8


def test_split_vertical_cells_are_merged_without_replacing_other_values():
    row = list(ROW)
    row[1], row[6] = "", ""
    continuation = [None, "10000", None, None, None, None, "105,0", None, None, None]
    facts = _parse(rows=[row, continuation, [None] * 10])
    assert facts[4]["value"] == "105.0"


@pytest.mark.parametrize("cells", [
    [[None, "10000"] + [None] * 8],
    [list(ROW), [None, "9999"] + [None] * 8],
])
def test_unbound_or_colliding_vertical_fragments_fail_closed(cells):
    with pytest.raises(ValueError):
        _parse(rows=cells)


def test_missing_current_value_is_not_inferred_from_neighbors():
    row = list(ROW)
    row[6] = None
    with pytest.raises(ValueError, match="five indices"):
        _parse(rows=[row])


@pytest.mark.parametrize("invalid", ["nan", "1e2", "-", "103,", "1.2.3", ""])
def test_decimal_parser_rejects_invalid_or_missing_cells(invalid):
    with pytest.raises(ValueError):
        tables._decimal(invalid)


def test_decimal_precision_is_preserved_without_rounding():
    assert tables._decimal("103,73") == "103.73"
    assert tables._decimal("1 234,500") == "1234.500"
    assert tables._decimal("-0,04") == "-0.04"


def _chars(text, *, superscript=False):
    return [dict(text=char, size=6.0 if superscript and index == len(text) - 1 else 9.0,
                 bottom=11.0 if superscript and index == len(text) - 1 else 14.0,
                 x0=float(index), x1=float(index + 1)) for index, char in enumerate(text)]


def test_superscript_marker_is_removed_using_geometry_not_string_suffix():
    assert tables._without_superscript("103,73", _chars("103,73", superscript=True)) == "103,7"
    assert tables._without_superscript("103,73", _chars("103,73")) == "103,73"


def test_non_trailing_small_numeric_glyph_is_not_blindly_stripped():
    chars = _chars("103,7")
    chars[1].update(size=6.0, bottom=11.0)
    with pytest.raises(ValueError, match="mixed-font"):
        tables._without_superscript("103,7", chars)


def test_supported_identity_is_complete_and_weights_are_not_emitted():
    facts = _parse()
    required = {"source_doc", "content_sha256", "source_page", "concept", "geography", "unit",
                "frequency", "category", "sector", "aggregation_level", "price_basis",
                "seasonal_adjustment", "source_variant", "period", "value", "fact_id", "locator"}
    assert all(required <= fact.keys() for fact in facts)
    assert {f["concept"] for f in facts} == {"cpi_index", "inflation_rate_mom", "inflation_rate_3m", "inflation_rate_yoy"}
    assert all(f["category"] == "all_items" and f["geography"] == "TG" for f in facts)
    assert not any(f["value"] == "10000" for f in facts)


def test_unreviewed_category_is_not_given_a_fabricated_identity():
    unknown = list(ROW)
    unknown[0] = "Unregistered basket"
    facts = _parse(rows=[unknown, list(ROW)])
    assert len(facts) == 8
    assert all(f["category"] == "all_items" for f in facts)


def test_country_alias_requires_exact_reviewed_geography():
    row = list(ROW)
    row[0] = "Burkina-Faso"
    facts = _parse(rows=[row], countries=True)
    assert all(f["geography"] == "BF" for f in facts)
    row[0] = "Burkina-Faso district"
    with pytest.raises(ValueError, match="Unsupported country"):
        _parse(rows=[row], countries=True)


@pytest.mark.parametrize("position,value", [(3, "unknown-26"), (6, "juin-26"), (9, "24mois")])
def test_unsupported_calendar_or_window_cannot_silently_reuse_mapping(position, value):
    header = list(HEADER)
    header[position] = value
    with pytest.raises(ValueError):
        _parse(header)


def test_wrong_profile_or_expected_hash_is_rejected_before_reading():
    with pytest.raises(ValueError, match="profile"):
        tables.read_source_facts(Path("not-present.pdf"), DOC, "0" * 64)
    with pytest.raises(ValueError, match="profile"):
        tables.read_source_facts(Path("not-present.pdf"), "unreviewed.pdf", DIGEST)


def test_changed_bytes_cannot_be_parsed(tmp_path, monkeypatch):
    path = tmp_path / "changed.pdf"
    path.write_bytes(b"changed source")
    monkeypatch.setattr(tables.pdfplumber, "open", lambda *args, **kwargs: pytest.fail("Changed bytes reached PDF parsing"))
    with pytest.raises(ValueError, match="source bytes"):
        tables.read_source_facts(path, DOC, DIGEST)


def test_profile_checksum_update_does_not_transfer_source_review(monkeypatch):
    profile = dict(tables.PROFILES[DOC], sha256="0" * 64)
    monkeypatch.setitem(tables.PROFILES, DOC, profile)
    with pytest.raises(ValueError, match="profile"):
        tables.read_source_facts(Path("not-present.pdf"), DOC, "0" * 64)


def test_malformed_table_cells_raise_bounded_value_error():
    with pytest.raises(ValueError, match="geometry"):
        _parse(rows=[[None, object()] + [None] * 8])
