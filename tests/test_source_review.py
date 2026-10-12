"""Read-only source-review evidence must never manufacture publication authority."""

import hashlib
import json
import os
import sqlite3

import pytest
import sqlalchemy as sa

from sika import source_review
from sika.inflation_mapping import PROFILES
from sika.migrate_inflation import migrate
from test_migrate_inflation import fixture as migration_fixture
from test_quality import DOC, pdf_bytes, store


def state(store):
    """Capture both file bytes and all protected audit/state counts."""
    target, engine, tables, _ = store
    with engine.connect() as con:
        counts = {name: con.execute(sa.select(sa.func.count()).select_from(tables[name])).scalar_one()
                  for name in ("quality_runs", "review_decisions", "publication_events")}
        statuses = {name: con.execute(sa.select(tables[name].c.status).order_by(tables[name].c.id)).scalars().all()
                    for name in ("releases", "observations")}
    return target.read_bytes(), counts, statuses


def findings(packet):
    return {finding["code"] for release in packet["releases"]
            for finding in release.get("findings", [])}


def source_fact(source=None):
    """Synthetic anchor, explicitly isolated from the official fact registry."""
    digest = hashlib.sha256(source.read_bytes()).hexdigest() if source else PROFILES[DOC]["sha256"]
    return dict(source_doc=DOC, content_sha256=digest,
                source_page=1, concept="inflation_rate_yoy", geography="TG", unit="percent",
                frequency="monthly", category="all_items", sector="all_sectors",
                aggregation_level="total", price_basis="not_applicable",
                seasonal_adjustment="not_reported", source_variant=PROFILES[DOC]["variant"],
                period="2026-05", value="0.4", fact_id="synthetic-only", locator="Fixture cell")


@pytest.mark.parametrize("key,replacement", [
    ("source_doc", "another.pdf"), ("content_sha256", "a" * 64), ("source_page", 2),
    ("concept", "inflation_rate_mom"), ("geography", "BJ"), ("unit", "index"),
    ("frequency", "annual"), ("category", "food"), ("sector", "agriculture"),
    ("aggregation_level", "group"), ("price_basis", "index_2014"),
    ("seasonal_adjustment", "adjusted"), ("source_variant", "other:methodology"),
    ("period", "2026-04"), ("value", "0.9"),
])
def test_source_fact_match_requires_the_complete_scope_not_just_a_number(key, replacement):
    fact = source_fact()
    observation = {k: v for k, v in fact.items() if k not in {"fact_id", "locator"}}
    assert source_review.compare_source_fact(observation, fact) == []
    observation[key] = replacement
    assert source_review.compare_source_fact(observation, fact) == [key]


def test_source_fact_numeric_comparison_is_exact_and_never_guesses_a_footnote():
    fact = source_fact()
    observation = {k: v for k, v in fact.items() if k not in {"fact_id", "locator"}}
    observation["value"] = "0.4000000000"
    assert source_review.compare_source_fact(observation, fact) == []
    observation["value"] = "NaN"
    assert source_review.compare_source_fact(observation, fact) == ["value"]
    fact["value"], observation["value"] = "103.7", "103.73"
    assert source_review.compare_source_fact(observation, fact) == ["value"]


@pytest.mark.parametrize("key", source_review.FACT_FIELDS)
def test_missing_scope_fields_never_equal_even_when_both_inputs_omit_them(key):
    fact = source_fact()
    observation = {k: v for k, v in fact.items() if k not in {"fact_id", "locator", key}}
    del fact[key]
    assert source_review.compare_source_fact(observation, fact) == [key]


def test_even_every_synthetic_anchor_matched_is_not_certification_or_approval(store, monkeypatch):
    target, _, _, source = store
    monkeypatch.setattr(source_review, "SPOT_CHECKS", (source_fact(source),))
    before = state(store)
    packet = source_review.build_review_packet(target, source_root=source.parent)
    assert packet["spot_check_count"] == packet["matched_observation_count"] == 1
    assert packet["pending_observation_count"] == 0
    assert packet["spot_checks"][0]["status"] == "matched"
    assert packet["releases"][0]["observations"][0]["source_check"] == "matched"
    assert packet["status"] == "needs_review"
    assert packet["certification_status"] == "pending"
    assert packet["publication_authorized"] is False
    assert "source_values_review_pending" in packet["releases"][0]["publication_blockers"]
    assert "verified_observation_ids" not in packet
    assert state(store) == before


def test_checksum_bound_anchor_mismatch_is_hard_and_never_repairs_the_row(store, monkeypatch):
    target, engine, tables, source = store
    monkeypatch.setattr(source_review, "SPOT_CHECKS", (source_fact(source),))
    with engine.begin() as con:
        con.execute(tables["observations"].update().values(value=0.9))
    before = state(store)
    packet = source_review.build_review_packet(target, source_root=source.parent)
    assert packet["status"] == "failed"
    assert packet["spot_checks"][0]["differences"] == {"1": ["value"]}
    assert "source_spot_check_mismatch" in findings(packet)
    assert packet["releases"][0]["observations"][0]["source_check"] == "mismatch"
    assert packet["publication_authorized"] is False
    assert state(store) == before


def test_source_change_during_read_is_explicit_hard_failure(store, monkeypatch):
    target, _, _, source = store
    original = source_review.quality._artifact
    calls = []

    def changed(*args):
        calls.append(1)
        artifact = original(*args)
        return artifact if len(calls) == 1 else artifact | {"sha256": "b" * 64}

    monkeypatch.setattr(source_review.quality, "_artifact", changed)
    before = state(store)
    packet = source_review.build_review_packet(target, source_root=source.parent)
    assert packet["status"] == "failed"
    assert "source_changed_during_review" in findings(packet)
    assert packet["publication_authorized"] is False
    assert state(store) == before


def test_repeated_review_packet_is_stable_read_only_and_never_authorizes(store):
    target, _, _, source = store
    before = state(store)
    first = source_review.build_review_packet(target, source_root=source.parent)
    second = source_review.build_review_packet(target, source_root=source.parent)
    assert first == second
    assert first["snapshot_sha256"] == second["snapshot_sha256"]
    for field in ("review_version", "status", "hard_failure_count", "observation_count",
                  "spot_check_count", "matched_observation_count", "pending_observation_count",
                  "quarantine_count", "releases"):
        assert first[field] == second[field]
    assert first["publication_authorized"] is False
    assert first["certification_status"] == "pending"
    assert first["status"] == "needs_review"
    assert first["hard_failure_count"] == 0
    assert first["observation_count"] == first["pending_observation_count"] == 1
    assert first["matched_observation_count"] == 0
    assert state(store) == before


def test_existing_quality_history_is_preserved_and_not_duplicated_by_review(store):
    target, _, _, source = store
    earlier_packet = source_review.build_review_packet(target, source_root=source.parent)
    source_review.quality.run_quality_checks(target, source_root=source.parent)
    before = state(store)
    assert before[1]["quality_runs"] == 1
    packet = source_review.build_review_packet(target, source_root=source.parent)
    assert packet == earlier_packet
    assert state(store) == before


def test_every_observation_retains_complete_identity_and_source_provenance(store):
    target, _, _, source = store
    packet = source_review.build_review_packet(target, source_root=source.parent)
    release = packet["releases"][0]
    assert release["source_doc"] == DOC
    assert release["observation_count"] == len(release["observations"]) == 1
    row = release["observations"][0]
    assert row["id"] == 1
    assert row["period"] == "2026-05"
    assert row["source_page"] == 1
    assert row["source_check"] == "pending"
    assert row["concept"] == "inflation_rate_yoy"
    assert row["geography"] == "TG"
    assert row["unit"] == "percent"
    assert row["frequency"] == "monthly"
    assert row["category"] == "all_items"
    assert row["price_basis"] == "not_applicable"
    assert row["source_variant"] == PROFILES[DOC]["variant"]


def test_unknown_source_metadata_remains_publication_blockers(store):
    target, _, _, source = store
    release = source_review.build_review_packet(target, source_root=source.parent)["releases"][0]
    assert {"source_values_review_pending", "source_rights_unverified", "publication_date_unverified",
            "revision_marks_unverified"}.issubset(release["publication_blockers"])


@pytest.mark.parametrize("license_url,attribution", [
    (" ", "INSEED"), ("https://example.test/license", " \t"),
    ("javascript:alert(1)", "INSEED"), ("/local/license", "INSEED"),
    ("https:///missing-host", "INSEED"), (None, "INSEED"),
])
def test_malformed_rights_evidence_stays_a_blocker_like_the_publication_gate(store, license_url, attribution):
    target, engine, tables, source = store
    with engine.begin() as con:
        con.execute(tables["sources"].update().values(reuse_status="allowed", license_url=license_url,
                                                    attribution=attribution))
    before = state(store)
    packet = source_review.build_review_packet(target, source_root=source.parent)
    assert "source_rights_unverified" in packet["releases"][0]["publication_blockers"]
    assert packet["publication_authorized"] is False
    assert state(store) == before


def test_source_cells_accept_repeated_identical_printed_evidence_without_choosing_by_confidence():
    fact = source_fact()
    row = dict(fact, id=1, release_id=1, source_check="pending")
    checks, failures = source_review.compare_source_cells([row], [fact, dict(fact, locator="Second table")])
    assert not failures
    assert checks[0]["status"] == row["source_check"] == "matched"
    assert len(checks[0]["cells"]) == 2


def test_a_conflicting_printed_cell_cannot_be_hidden_by_a_matching_one():
    fact = source_fact()
    row = dict(fact, id=1, release_id=1, source_check="matched")
    checks, failures = source_review.compare_source_cells([row], [fact, dict(fact, value="0.9")])
    assert checks[0]["status"] == row["source_check"] == "mismatch"
    assert [f.code for f in failures] == ["source_cell_mismatch"]


@pytest.mark.parametrize("field,new_value", [("period", "2026-04"), ("source_page", 2),
                                            ("unit", "index"), ("category", "food")])
def test_source_cell_scope_mismatch_is_not_a_numeric_match(field, new_value):
    fact = source_fact()
    row = dict(fact, id=1, release_id=1, source_check="pending")
    row[field] = new_value
    checks, failures = source_review.compare_source_cells([row], [fact])
    assert checks[0]["status"] == "missing"
    assert row["source_check"] == "mismatch"
    assert [f.code for f in failures] == ["source_cell_missing"]


@pytest.mark.parametrize("fault,expected", [
    ("missing", "source_file_missing"),
    ("changed", "source_checksum_mismatch"),
    ("outside", "source_outside_archive"),
])
def test_artifact_corruption_or_escape_fails_closed_without_database_writes(store, tmp_path, fault, expected):
    target, _, _, source = store
    source_root = source.parent
    if fault == "missing":
        source.unlink()
    elif fault == "changed":
        source.write_bytes(pdf_bytes(2))
    else:
        source_root = tmp_path / "not-the-archive"
        source_root.mkdir()
    before = state(store)
    packet = source_review.build_review_packet(target, source_root=source_root)
    assert packet["status"] == "failed"
    assert packet["hard_failure_count"] > 0
    assert packet["publication_authorized"] is False
    assert expected in findings(packet)
    assert packet["matched_observation_count"] == 0
    assert state(store) == before


def test_invalid_period_is_a_hard_failure_and_never_repaired(store):
    target, engine, tables, source = store
    with engine.begin() as con:
        con.execute(tables["observations"].update().values(period="2026-13"))
    before = state(store)
    packet = source_review.build_review_packet(target, source_root=source.parent)
    assert packet["status"] == "failed"
    assert "invalid_period" in findings(packet)
    assert state(store) == before


def test_orphan_observation_does_not_disappear_in_review_packet(store):
    target, engine, _, source = store
    con = engine.raw_connection()
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("UPDATE observations SET series_id=999")
        con.commit()
    finally:
        con.execute("PRAGMA foreign_keys=ON")
        con.close()
    before = state(store)
    packet = source_review.build_review_packet(target, source_root=source.parent)
    assert packet["observation_count"] == 1
    row = packet["releases"][0]["observations"][0]
    assert row["id"] == 1
    assert row["concept"] is None
    assert packet["status"] == "failed"
    assert "observation_reference_missing" in findings(packet)
    assert state(store) == before


def test_nonexistent_or_legacy_target_is_rejected_without_creating_or_modifying_it(tmp_path):
    missing = tmp_path / "absent.db"
    with pytest.raises((FileNotFoundError, ValueError)):
        source_review.build_review_packet(missing)
    assert not missing.exists()
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as con:
        con.execute("CREATE TABLE observations(id INTEGER PRIMARY KEY)")
    before = legacy.read_bytes()
    with pytest.raises(ValueError):
        source_review.build_review_packet(legacy)
    assert legacy.read_bytes() == before


def test_target_cannot_also_be_legacy(store):
    target, _, _, source = store
    before = state(store)
    with pytest.raises(ValueError):
        source_review.build_review_packet(target, legacy=target, source_root=source.parent)
    assert state(store) == before


def test_wrong_schema_revision_is_rejected(store):
    target, engine, _, source = store
    with engine.begin() as con:
        con.exec_driver_sql("UPDATE alembic_version SET version_num='0002_inflation_audit'")
    before = state(store)
    with pytest.raises(ValueError):
        source_review.build_review_packet(target, source_root=source.parent)
    assert state(store) == before


def test_each_quarantined_original_is_reported_without_disposition_changes(migration_fixture, monkeypatch):
    legacy, target, raw, _ = migration_fixture
    artifact = pdf_bytes(7)
    (raw / DOC).write_bytes(artifact)
    monkeypatch.setitem(PROFILES, DOC, PROFILES[DOC] | {"sha256": hashlib.sha256(artifact).hexdigest()})
    migrate(legacy, target, raw)
    before_target, before_legacy = target.read_bytes(), legacy.read_bytes()
    packet = source_review.build_review_packet(target, legacy=legacy)
    assert packet["quarantine_count"] == 1
    assert [row["legacy_id"] for row in packet["quarantine"]] == [4]
    quarantined = packet["quarantine"][0]
    assert quarantined["source_doc"] == DOC
    assert quarantined["source_page"] == 1
    assert quarantined["original_row"]["value"] == 99
    assert quarantined["reason"]
    assert packet["publication_authorized"] is False
    assert target.read_bytes() == before_target
    assert legacy.read_bytes() == before_legacy


def test_migrated_packet_needs_exact_legacy_snapshot(migration_fixture, monkeypatch):
    legacy, target, raw, _ = migration_fixture
    artifact = pdf_bytes(7)
    (raw / DOC).write_bytes(artifact)
    monkeypatch.setitem(PROFILES, DOC, PROFILES[DOC] | {"sha256": hashlib.sha256(artifact).hexdigest()})
    migrate(legacy, target, raw)
    packet = source_review.build_review_packet(target)
    assert packet["status"] == "failed"
    assert "legacy_snapshot_unverified" in findings(packet)


def test_cli_report_cannot_overwrite_exact_legacy_snapshot(migration_fixture, monkeypatch, tmp_path):
    legacy, target, raw, _ = migration_fixture
    artifact = pdf_bytes(7)
    (raw / DOC).write_bytes(artifact)
    monkeypatch.setitem(PROFILES, DOC, PROFILES[DOC] | {"sha256": hashlib.sha256(artifact).hexdigest()})
    migrate(legacy, target, raw)
    alias = tmp_path / "legacy-report.json"
    os.link(legacy, alias)
    before = legacy.read_bytes()
    assert source_review.main(["--target", str(target), "--legacy", str(legacy),
                               "--report", str(alias)]) == 2
    assert legacy.read_bytes() == before


def test_cli_generates_pending_packet_without_granting_authority(store, tmp_path, capsys):
    target, _, _, source = store
    report = tmp_path / "source-review.json"
    before = state(store)
    args = ["--target", str(target), "--source-root", str(source.parent)]
    assert source_review.main(args + ["--report", str(report)]) == 0
    packet = json.loads(report.read_text(encoding="utf-8"))
    assert packet["status"] == "needs_review"
    assert packet["publication_authorized"] is False
    assert "publication" in capsys.readouterr().out.lower()
    assert state(store) == before


def test_cli_report_cannot_overwrite_database_or_source(store, tmp_path):
    target, _, _, source = store
    args = ["--target", str(target), "--source-root", str(source.parent)]
    target_before, source_before = target.read_bytes(), source.read_bytes()
    for protected in (target, source):
        assert source_review.main(args + ["--report", str(protected)]) == 2
    assert target.read_bytes() == target_before
    assert source.read_bytes() == source_before
    assert source_review.main(args + ["--report", str(tmp_path / "unsafe.pdf")]) == 2


@pytest.mark.parametrize("protected", ["target", "source"])
def test_json_extension_cannot_hide_report_hardlink_to_protected_file(store, tmp_path, protected):
    target, _, _, source = store
    original = target if protected == "target" else source
    alias = tmp_path / f"{protected}-alias.json"
    os.link(original, alias)
    before = original.read_bytes()
    assert source_review.main(["--target", str(target), "--source-root", str(source.parent),
                               "--report", str(alias)]) == 2
    assert original.read_bytes() == before


def test_cli_exit_codes_for_hard_failure_and_missing_target(store, tmp_path):
    target, engine, tables, source = store
    with engine.begin() as con:
        con.execute(tables["observations"].update().values(period="2026-13"))
    assert source_review.main(["--target", str(target), "--source-root", str(source.parent)]) == 1
    missing = tmp_path / "absent.db"
    assert source_review.main(["--target", str(missing)]) == 2
    assert not missing.exists()


def test_public_summary_retains_evidence_coverage_without_draft_values_or_originals(store):
    target, _, _, source = store
    full = source_review.build_review_packet(target, source_root=source.parent)
    redacted = source_review.public_summary(full)
    assert redacted["summary_data_redacted"] is True
    assert redacted["review_packet_sha256"] == full["review_packet_sha256"]
    assert redacted["snapshot_sha256"] == full["snapshot_sha256"]
    assert redacted["observation_count"] == 1
    assert redacted["publication_authorized"] is False
    assert redacted["releases"][0]["source_checks"][0]["observation_id"] == 1

    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    assert not {"value", "original_label", "original_row", "observations", "expected", "cells",
                "locator", "storage_uri", "attribution", "license_url"} & keys(redacted)


def test_cli_summary_export_is_redacted_and_byte_stable_without_database_writes(store, tmp_path):
    target, _, _, source = store
    report = tmp_path / "public-summary.json"
    args = ["--target", str(target), "--source-root", str(source.parent), "--summary-only", "--report", str(report)]
    before = state(store)
    assert source_review.main(args) == 0
    first = report.read_bytes()
    assert source_review.main(args) == 0
    assert report.read_bytes() == first
    assert json.loads(first)["summary_data_redacted"] is True
    assert state(store) == before
