"""V2 validation evidence, corruption detection and transaction boundaries."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from sika.database import create_database_engine
from sika.inflation_mapping import PROFILES
from sika import quality
from sika import validate
from test_migrate_inflation import fixture as migration_fixture

ROOT = Path(__file__).resolve().parents[1]
DOC = "inseed_ihpc_2026-05.pdf"


def pdf_bytes(pages=1):
    """Minimal synthetic PDF for testing byte/page verification, not source figures."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               f"<< /Type /Pages /Kids [{' '.join(f'{i+3} 0 R' for i in range(pages))}] /Count {pages} >>".encode()]
    objects.extend(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>" for _ in range(pages))
    document = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(document))
        document += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(document)
    document += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    document += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    return document + f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


@pytest.fixture
def store(tmp_path, monkeypatch):
    target = tmp_path / "quality.db"
    source_root = tmp_path / "trust_core_sources"
    source_root.mkdir()
    artifact = pdf_bytes()
    digest = hashlib.sha256(artifact).hexdigest()
    source = source_root / f"{digest}.pdf"
    source.write_bytes(artifact)
    monkeypatch.setitem(PROFILES, DOC, PROFILES[DOC] | {"sha256": digest, "pages": 1})
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", str(sa.URL.create("sqlite", database=str(target))).replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_database_engine(sa.URL.create("sqlite", database=str(target)))
    metadata = sa.MetaData()
    metadata.reflect(engine)
    tables = metadata.tables
    with engine.begin() as con:
        for name, values in (
            ("concepts", dict(id=1, code="inflation_rate_yoy", label_fr="Inflation annuelle")),
            ("geographies", dict(id=1, code="TG", label_fr="Togo")),
            ("units", dict(id=1, code="percent", label_fr="Pourcentage", symbol="%", quantity="rate", scale=1)),
            ("dimensions", dict(id=1, category="all_items", sector="all_sectors", aggregation_level="total",
                                price_basis="not_applicable", seasonal_adjustment="not_reported", source_variant=PROFILES[DOC]["variant"])),
            ("series", dict(id=1, series_key="test.inflation", concept_id=1, geography_id=1, unit_id=1,
                            dimensions_id=1, frequency="monthly", label_fr="Inflation annuelle")),
            ("sources", dict(id=1, source_key="inseed.ihpc", publisher="INSEED", title="Synthetic publication", canonical_url=PROFILES[DOC]["url"])),
            ("releases", dict(id=1, release_key="test.release", source_id=1, source_doc=DOC,
                              source_url=PROFILES[DOC]["url"], publisher="INSEED", content_sha256=digest,
                              retrieved_at=datetime.now(timezone.utc), storage_uri=source.as_uri(), media_type="application/pdf")),
            ("observations", dict(id=1, series_id=1, release_id=1, period="2026-05", value=0.4,
                                  source_page=1, original_label="Indice global", confidence=0.95)),
        ):
            con.execute(tables[name].insert().values(**values))
    yield target, engine, tables, source
    engine.dispose()


def snapshot(store):
    _, engine, tables, source = store
    with engine.connect() as con:
        data = quality._read_tables(con, tables)
    return data, {1: quality._artifact(data["releases"][0], source.parent)}


def codes(findings):
    return {f.code for f in findings}


@pytest.mark.parametrize("period,frequency,expected", [
    ("2026", "annual", True), ("2026-Q4", "quarterly", True), ("2026-06", "monthly", True),
    ("2024-02-29", "daily", True), ("2026-02-29", "daily", False),
    ("2026-04-31", "daily", False), ("0000", "annual", False),
    ("2026-Q0", "quarterly", False), ("2026-Q5", "quarterly", False),
    ("2026-00", "monthly", False), ("2026-13", "monthly", False),
    ("2026-6", "monthly", False), ("2026-Q1", "monthly", False),
    ("2026-01-01", "irregular", False),
])
def test_period_calendar_and_frequency(period, frequency, expected):
    assert quality.valid_period(period, frequency) is expected


def test_runs_bind_the_same_snapshot_and_preserve_states(store):
    target, engine, tables, _ = store
    first = quality.run_quality_checks(target)
    second = quality.run_quality_checks(target)
    assert first["status"] == second["status"] == "passed"
    assert first["snapshot_sha256"] == second["snapshot_sha256"]
    assert first["releases"][0]["findings"] == second["releases"][0]["findings"]
    assert first["publication_authorized"] is False
    assert first["releases"][0]["quality_run_id"] != second["releases"][0]["quality_run_id"]
    with engine.connect() as con:
        rows = con.execute(sa.select(tables["quality_runs"])).mappings().all()
        assert len(rows) == 2
        for row in rows:
            assert row["status"] == row["report"]["status"] == "passed"
            assert row["hard_failure_count"] == 0
            assert row["warning_count"] == len(row["report"]["findings"])
            assert row["completed_at"] >= row["started_at"]
        assert con.execute(sa.select(tables["observations"].c.status)).scalar_one() == "draft"
        assert con.execute(sa.select(tables["releases"].c.status)).scalar_one() == "draft"
        assert con.execute(sa.select(sa.func.count()).select_from(tables["review_decisions"])).scalar_one() == 0


@pytest.mark.parametrize("value", ["not numeric", "NaN", "Infinity", "1000000000000000000", "0.12345678901"])
def test_bad_numeric_storage_is_a_recorded_hard_failure(store, value):
    target, engine, _, _ = store
    with engine.begin() as con:
        con.exec_driver_sql("UPDATE observations SET value=?", (value,))
    result = quality.run_quality_checks(target)
    assert result["status"] == "failed"
    assert "invalid_value" in {r["code"] for r in result["releases"][0]["findings"]}


def test_invalid_period_is_persisted_without_publishing(store):
    target, engine, tables, _ = store
    with engine.begin() as con:
        con.execute(tables["observations"].update().values(period="2026-13"))
    result = quality.run_quality_checks(target)
    assert result["status"] == "failed"
    assert "invalid_period" in {f["code"] for f in result["releases"][0]["findings"]}


def test_orphan_fk_cannot_disappear_through_a_join(store):
    target, _, _, _ = store
    with sqlite3.connect(target) as con:
        con.execute("UPDATE observations SET series_id=999")
    result = quality.run_quality_checks(target)
    assert result["status"] == "failed"
    assert result["releases"][0]["observation_count"] == 1
    assert "observation_reference_missing" in {f["code"] for f in result["releases"][0]["findings"]}


@pytest.mark.parametrize("mutate,expected", [
    (lambda s: s["units"][0].update(code="index"), "unit_concept_mismatch"),
    (lambda s: s["units"][0].update(scale="0.01"), "unit_concept_mismatch"),
    (lambda s: s["dimensions"][0].update(category="unknown"), "dimension_not_reviewed"),
    (lambda s: s["dimensions"][0].update(price_basis="index_2014"), "price_basis_mismatch"),
    (lambda s: s["dimensions"][0].update(source_variant="inseed:bms_2014_as_printed"), "source_methodology_mismatch"),
    (lambda s: s["series"][0].update(concept_id=999), "series_reference_missing"),
    (lambda s: s["observations"][0].update(source_page=2), "invalid_observation_provenance"),
    (lambda s: s["passages"].append(dict(id=1,release_id=1,source_page=2,text="Context")), "invalid_passage_provenance"),
])
def test_complete_identity_and_citations_are_checked(store, mutate, expected):
    data, artifacts = snapshot(store)
    mutate(data)
    assert expected in codes(quality.examine(data, artifacts))


def test_duplicate_complete_identity_is_hard_but_cross_release_repeat_is_valid(store):
    data, artifacts = snapshot(store)
    data["observations"].append(data["observations"][0] | {"id": 2})
    assert "duplicate_observation" in codes(quality.examine(data, artifacts))
    data["releases"].append(data["releases"][0] | {"id": 2, "release_key": "metadata-corrected"})
    data["observations"][1]["release_id"] = 2
    assert "duplicate_observation" not in codes(quality.examine(data, artifacts | {2: artifacts[1]}))


def test_duplicate_series_identity_is_detected_even_on_different_periods(store):
    data, artifacts = snapshot(store)
    data["series"].append(data["series"][0] | {"id": 2, "series_key": "alias.key"})
    data["observations"].append(data["observations"][0] | {"id": 2, "series_id": 2, "period": "2026-04"})
    assert "duplicate_series_identity" in codes(quality.examine(data, artifacts))


def test_outlier_warning_does_not_change_the_value_or_fail_the_series(store):
    data, artifacts = snapshot(store)
    data["observations"] = [data["observations"][0] | {"id": i+1, "period": f"{2000+i//12}-{i%12+1:02d}", "value": "1"} for i in range(30)]
    data["observations"][-1]["value"] = "99"
    findings = quality.examine(data, artifacts)
    assert [f.observation_id for f in findings if f.code == "value_outlier"] == [30]
    assert not [f for f in findings if f.severity == "FAIL"]
    assert data["observations"][-1]["value"] == "99"


@pytest.mark.parametrize("fault,expected", [("missing", "source_file_missing"), ("changed", "source_checksum_mismatch")])
def test_archive_failures_are_hard(store, fault, expected):
    target, _, _, source = store
    if fault == "missing":
        source.unlink()
    else:
        source.write_bytes(pdf_bytes(2))
    result = quality.run_quality_checks(target)
    assert result["status"] == "failed"
    assert expected in {f["code"] for f in result["releases"][0]["findings"]}


def test_archive_root_prevents_reading_arbitrary_local_paths(store, tmp_path):
    data, _ = snapshot(store)
    data["releases"][0]["storage_uri"] = (tmp_path / "outside.pdf").as_uri()
    assert quality._artifact(data["releases"][0], tmp_path / "trust_core_sources") == {"error": "source_outside_archive"}


def test_changed_content_changes_fingerprint(store):
    target, engine, tables, _ = store
    before = quality.run_quality_checks(target)
    with engine.begin() as con:
        con.execute(tables["observations"].update().values(value=0.5))
    after = quality.run_quality_checks(target)
    assert before["snapshot_sha256"] != after["snapshot_sha256"]


def test_multi_release_evidence_is_all_or_nothing(store, monkeypatch):
    target, engine, tables, _ = store
    with engine.begin() as con:
        first = dict(con.execute(sa.select(tables["releases"])).mappings().one())
        con.execute(tables["releases"].insert().values(first | {"id": 2, "release_key": "second-vintage"}))
        first_ob = dict(con.execute(sa.select(tables["observations"])).mappings().one())
        con.execute(tables["observations"].insert().values(first_ob | {"id": 2, "release_id": 2}))
    original = quality._store_run
    calls = []
    def interrupt(*args):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("Interrupted after the first run insert")
        return original(*args)
    monkeypatch.setattr(quality, "_store_run", interrupt)
    with pytest.raises(RuntimeError, match="Interrupted"):
        quality.run_quality_checks(target)
    with engine.connect() as con:
        assert con.execute(sa.select(sa.func.count()).select_from(tables["quality_runs"])).scalar_one() == 0


def test_source_change_during_validation_cannot_pass(store, monkeypatch):
    target, _, _, _ = store
    original = quality._artifact
    calls = []
    def changed(*args):
        calls.append(1)
        result = original(*args)
        return result if len(calls) == 1 else result | {"sha256": "b" * 64}
    monkeypatch.setattr(quality, "_artifact", changed)
    result = quality.run_quality_checks(target)
    assert result["status"] == "failed"
    assert "source_changed_during_validation" in {f["code"] for f in result["releases"][0]["findings"]}


def test_migration_audit_requires_exact_legacy_and_detects_deleted_quarantine(migration_fixture, monkeypatch):
    from sika.migrate_inflation import migrate
    legacy, target, raw, _ = migration_fixture
    artifact = pdf_bytes(7)
    (raw / DOC).write_bytes(artifact)
    monkeypatch.setitem(PROFILES, DOC, PROFILES[DOC] | {"sha256": hashlib.sha256(artifact).hexdigest()})
    migrate(legacy, target, raw)
    assert quality.run_quality_checks(target)["status"] == "failed"
    result = quality.run_quality_checks(target, legacy=legacy)
    assert result["status"] == "passed"
    assert "legacy_quarantine" in {f["code"] for f in result["releases"][0]["findings"]}
    with sqlite3.connect(target) as con:
        con.execute("DELETE FROM inflation_migration_rows WHERE disposition='quarantined'")
    result = quality.run_quality_checks(target, legacy=legacy)
    assert result["status"] == "failed"
    assert "migration_id_coverage" in {f["code"] for f in result["releases"][0]["findings"]}


def test_deleted_migration_audit_cannot_hide_preserved_legacy_links(store):
    data, artifacts = snapshot(store)
    data["legacy_observation_links"].append(dict(legacy_database="sha256:test", legacy_id=1,
                                               observation_id=1, original_row={"id": 1}))
    assert "orphan_migration_audit" in codes(quality.examine(data, artifacts))


def test_cli_failure_codes_and_explicit_target_safety(store, tmp_path, capsys):
    target, engine, tables, _ = store
    report = tmp_path / "quality.json"
    args = ["--target", str(target)]
    assert validate.main(args + ["--report", str(target)]) == 2
    assert validate.main(args + ["--report", str(report)]) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["publication_authorized"] is False
    with engine.begin() as con:
        con.execute(tables["observations"].update().values(period="2026-Q0"))
    assert validate.main(args) == 1
    assert validate.main(args + ["--release-id", "999"]) == 2
    missing = tmp_path / "does-not-exist.db"
    assert validate.main(["--target", str(missing)]) == 2
    assert not missing.exists()
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as con:
        con.execute("CREATE TABLE observations(id INTEGER PRIMARY KEY)")
    before = legacy.read_bytes()
    assert validate.main(["--target", str(legacy)]) == 2
    assert legacy.read_bytes() == before
    assert "Technical validation only" in capsys.readouterr().out
