"""S0.3 reconciliation, quarantine, interruption and replay contracts."""

import hashlib
import json
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from sika import migrate_inflation as migration
from sika.inflation_mapping import PROFILES, map_row

ROOT = Path(__file__).resolve().parents[1]
DOC = "inseed_ihpc_2026-05.pdf"


def row(legacy_id=1, **overrides):
    return dict(id=legacy_id, indicator="inflation_rate_yoy", indicator_label="INDICE GLOBAL",
                geography="Togo", period="2026-05", value=0.4, unit="%", source_doc=DOC,
                source_page=1, confidence=0.95) | overrides


def append_rows(path, rows):
    with sqlite3.connect(path) as con:
        con.executemany("INSERT INTO observations VALUES (:id,:indicator,:indicator_label,:geography,:period,:value,:unit,:source_doc,:source_page,:confidence)", rows)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.db"
    target = tmp_path / "canonical.db"
    raw = tmp_path / "raw"
    raw.mkdir()
    # The importer verifies bytes against the registry, not parser behavior. This
    # synthetic artifact is explicitly substituted for the real source in this test.
    artifact = b"Synthetic source bytes for the migration tests, not an official PDF"
    (raw / DOC).write_bytes(artifact)
    monkeypatch.setitem(PROFILES, DOC, PROFILES[DOC] | {"sha256": hashlib.sha256(artifact).hexdigest()})
    with sqlite3.connect(legacy) as con:
        con.executescript("""
            CREATE TABLE observations(id INTEGER PRIMARY KEY, indicator TEXT, indicator_label TEXT,
                geography TEXT, period TEXT, value REAL, unit TEXT, source_doc TEXT,
                source_page INTEGER, confidence REAL);
            CREATE TABLE passages(id INTEGER PRIMARY KEY, source_doc TEXT, page INTEGER, text TEXT);
        """)
        con.execute("INSERT INTO passages VALUES (1, ?, 1, 'Fixture source text')", (DOC,))
    append_rows(legacy, [row(), row(2, indicator_label="IHPC au Togo (glissement annuel)", source_page=4),
        row(3, indicator_label="Produits alimentaires et boissons non alcoolisées", value=-1.4),
        row(4, indicator_label="Unknown scope", value=99), row(5, indicator="gdp_growth", value=5)])
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", str(sa.URL.create("sqlite", database=str(target))).replace("%", "%%"))
    command.upgrade(config, "head")
    return legacy, target, raw, config


def table_count(target, table):
    with sqlite3.connect(target) as con:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_migration_preserves_values_ids_pages_and_does_not_publish(fixture):
    legacy, target, raw, _ = fixture
    before = legacy.read_bytes()
    report = migration.migrate(legacy, target, raw)
    assert report["result"] == "PASS"
    assert report["input_rows"] == 4
    assert report["dispositions"] == {"mapped": 2, "merged": 1, "quarantined": 1}
    assert report["canonical_observations"] == 2
    assert legacy.read_bytes() == before
    with sqlite3.connect(target) as con:
        assert con.execute("SELECT DISTINCT status FROM observations").fetchall() == [("draft",)]
        assert con.execute("SELECT source_page FROM inflation_migration_rows ORDER BY legacy_id").fetchall() == [(1,), (4,), (1,), (1,)]
        original = json.loads(con.execute("SELECT original_row FROM legacy_observation_links WHERE legacy_id=2").fetchone()[0])
        assert original["source_page"] == 4 and original["value"] == 0.4
        quarantined = json.loads(con.execute("SELECT original_row FROM inflation_migration_rows WHERE legacy_id=4").fetchone()[0])
        assert quarantined == row(4, indicator_label="Unknown scope", value=99)
        assert con.execute("SELECT text FROM passages").fetchone()[0] == "Fixture source text"
        assert con.execute("SELECT published_on FROM releases").fetchone()[0] is None


def test_exact_replay_is_idempotent_and_independently_reconciles(fixture):
    legacy, target, raw, _ = fixture
    first = migration.migrate(legacy, target, raw)
    second = migration.migrate(legacy, target, raw)
    assert first == second
    assert table_count(target, "inflation_migration_runs") == 1
    assert table_count(target, "legacy_observation_links") == 3
    with sqlite3.connect(target) as con:
        con.execute("UPDATE observations SET value=77 WHERE id=1")
    with pytest.raises(ValueError, match="Reconciliation failed"):
        migration.migrate(legacy, target, raw)


def test_conflicting_values_quarantine_every_candidate_without_selecting_confidence(fixture):
    legacy, target, raw, _ = fixture
    with sqlite3.connect(legacy) as con:
        con.execute("UPDATE observations SET value=0.7,confidence=1 WHERE id=2")
    report = migration.migrate(legacy, target, raw)
    assert report["quarantine_reasons"]["conflicting_legacy_values"] == 2
    assert report["canonical_observations"] == 1


@pytest.mark.parametrize("fault", ["missing", "changed"])
def test_unverified_documents_never_create_canonical_observations(fixture, fault):
    legacy, target, raw, _ = fixture
    if fault == "missing":
        (raw / DOC).unlink()
    else:
        (raw / DOC).write_bytes(b"Unreviewed replacement")
    report = migration.migrate(legacy, target, raw)
    assert report["canonical_observations"] == 0
    assert report["dispositions"] == {"quarantined": 4}
    assert report["passage_dispositions"] == {"quarantined": 1}


def test_unsafe_document_path_is_preserved_but_not_opened(fixture):
    legacy, target, raw, _ = fixture
    append_rows(legacy, [row(6, source_doc="../outside.pdf")])
    report = migration.migrate(legacy, target, raw)
    assert report["quarantine_reasons"]["unsafe_source_filename"] == 1


def test_changed_legacy_snapshot_requires_a_new_target(fixture):
    legacy, target, raw, _ = fixture
    migration.migrate(legacy, target, raw)
    append_rows(legacy, [row(6, period="2026-04")])
    with pytest.raises(ValueError, match="different import"):
        migration.migrate(legacy, target, raw)
    assert table_count(target, "inflation_migration_rows") == 4


def test_active_legacy_journal_is_rejected_before_writing_target(fixture):
    legacy, target, raw, _ = fixture
    Path(str(legacy) + "-wal").write_bytes(b"Active journal fixture")
    with pytest.raises(ValueError, match="active journal"):
        migration.migrate(legacy, target, raw)
    assert table_count(target, "inflation_migration_runs") == 0


def test_equal_values_across_releases_remain_distinct_vintages(fixture, monkeypatch):
    legacy, target, raw, _ = fixture
    second_doc = "inseed_ihpc_2026-06.pdf"
    artifact = b"Second synthetic publication"
    (raw / second_doc).write_bytes(artifact)
    monkeypatch.setitem(PROFILES, second_doc,
                        PROFILES[second_doc] | {"sha256": hashlib.sha256(artifact).hexdigest()})
    append_rows(legacy, [row(6, source_doc=second_doc)])
    report = migration.migrate(legacy, target, raw)
    assert report["canonical_observations"] == 3
    assert report["canonical_series"] == 2
    assert table_count(target, "releases") == 2
    assert report["dispositions"]["merged"] == 1


def test_interrupted_import_rolls_back_and_can_resume(fixture, monkeypatch):
    legacy, target, raw, _ = fixture
    copy_passages = migration._copy_passages
    def fail(*args, **kwargs):
        raise RuntimeError("Simulated interruption after observation writes")
    monkeypatch.setattr(migration, "_copy_passages", fail)
    with pytest.raises(RuntimeError, match="Simulated interruption"):
        migration.migrate(legacy, target, raw)
    assert table_count(target, "observations") == 0
    assert table_count(target, "releases") == 0
    assert table_count(target, "inflation_migration_runs") == 0
    monkeypatch.setattr(migration, "_copy_passages", copy_passages)
    assert migration.migrate(legacy, target, raw)["result"] == "PASS"


def test_populated_import_can_downgrade_and_rebuild_without_changing_legacy(fixture):
    legacy, target, raw, config = fixture
    before = legacy.read_bytes()
    original = migration.migrate(legacy, target, raw)
    command.downgrade(config, "base")
    assert table_count(target, "alembic_version") == 0
    command.upgrade(config, "head")
    assert migration.migrate(legacy, target, raw) == original
    assert legacy.read_bytes() == before


def test_legacy_cannot_be_target(fixture):
    legacy, _, raw, _ = fixture
    before = legacy.read_bytes()
    with pytest.raises(ValueError, match="must differ"):
        migration.migrate(legacy, legacy, raw)
    assert legacy.read_bytes() == before


def test_nonempty_unrelated_target_is_rejected(fixture):
    legacy, target, raw, _ = fixture
    with sqlite3.connect(target) as con:
        con.execute("INSERT INTO concepts(code,label_fr) VALUES ('existing','Existing catalog')")
    with pytest.raises(ValueError, match="empty Trust Core"):
        migration.migrate(legacy, target, raw)
    assert table_count(target, "concepts") == 1


def test_changed_archive_is_detected_on_replay(fixture):
    legacy, target, raw, _ = fixture
    migration.migrate(legacy, target, raw)
    archived = target.parent / "trust_core_sources" / f"{PROFILES[DOC]['sha256']}.pdf"
    archived.write_bytes(b"Changed after import")
    with pytest.raises(ValueError, match="checksum mismatch"):
        migration.migrate(legacy, target, raw)


def test_conflicting_passages_keep_both_originals(fixture):
    legacy, target, raw, _ = fixture
    with sqlite3.connect(legacy) as con:
        con.execute("INSERT INTO passages VALUES (2, ?, 1, 'Different extraction')", (DOC,))
    report = migration.migrate(legacy, target, raw)
    assert report["input_passages"] == 2
    assert report["passage_dispositions"] == {"quarantined": 2}
    assert table_count(target, "inflation_migration_passages") == 2
    assert table_count(target, "passages") == 0


def test_identical_passages_merge_with_both_legacy_ids(fixture):
    legacy, target, raw, _ = fixture
    with sqlite3.connect(legacy) as con:
        con.execute("INSERT INTO passages VALUES (2, ?, 1, 'Fixture source text')", (DOC,))
    report = migration.migrate(legacy, target, raw)
    assert report["passage_dispositions"] == {"mapped": 1, "merged": 1}
    assert table_count(target, "inflation_migration_passages") == 2
    assert table_count(target, "passages") == 1


def test_replay_detects_changed_passage_citation(fixture):
    legacy, target, raw, _ = fixture
    migration.migrate(legacy, target, raw)
    with sqlite3.connect(target) as con:
        con.execute("UPDATE inflation_migration_passages SET source_page=2")
    with pytest.raises(ValueError, match="raw_passage"):
        migration.migrate(legacy, target, raw)


@pytest.mark.parametrize("updates,reason", [
    ({"indicator_label": "Produits alimentaires et inconnus"}, "category_not_reviewed"),
    ({"period": "2026-13"}, "invalid_period"), ({"value": 0.12345678901}, "unsupported_numeric_value"),
    ({"source_page": 0}, "invalid_source_page"), ({"confidence": 0.3}, "legacy_confidence_excluded"),
    ({"unit": "points"}, "unit_not_reviewed"),
    ({"indicator_label": "Variation des prix depuis 12 mois - Bénin"}, "label_geography_conflict"),
    ({"indicator": "cpi_subindex_weight", "unit": "‰"}, "weight_scale_not_reviewed"),
])
def test_invalid_or_ambiguous_mapping_is_explicitly_quarantined(updates, reason):
    mapping, actual_reason = map_row(row(**updates), PROFILES[DOC])
    assert mapping is None and actual_reason == reason


def test_monthly_quarterly_and_rolling_average_measures_do_not_merge():
    annual_change, _ = map_row(row(), PROFILES[DOC])
    monthly_change, _ = map_row(row(indicator="inflation_rate_mom"), PROFILES[DOC])
    rolling_quarter, _ = map_row(row(indicator="inflation_rate_qoq",
        indicator_label="niveau général des prix en évolution trimestrielle"), PROFILES[DOC])
    average, _ = map_row(row(indicator="inflation_rate_12m_average",
        indicator_label="taux d’inflation, calculé sur la base des indices moyens des douze derniers mois au niveau national"), PROFILES[DOC])
    assert len({m.concept for m in (annual_change, monthly_change, rolling_quarter, average)}) == 4
    assert rolling_quarter.frequency == "monthly"


def test_base_2014_and_2023_indices_remain_distinct():
    index_row = row(indicator="consumer_price_index", unit="index", value=105)
    current, _ = map_row(index_row, PROFILES[DOC])
    old_doc = "inseed_bulletin_mensuel_2025-09.pdf"
    old, _ = map_row(index_row | {"source_doc": old_doc, "source_page": 8}, PROFILES[old_doc])
    assert current.price_basis == "index_2023"
    assert old.price_basis == "index_2014"
    assert current.source_variant != old.source_variant


def test_forecasts_and_ambiguous_inflation_window_are_quarantined():
    doc = "bceao_politique_monetaire_2023-06.pdf"
    mapping, reason = map_row(row(source_doc=doc, geography="UEMOA", period="2024",
        indicator_label="taux d'inflation", source_page=15), PROFILES[doc])
    assert mapping is None and reason == "forecast_not_observation"
    doc = "inseed_bulletin_mensuel_2025-09.pdf"
    mapping, reason = map_row(row(source_doc=doc, indicator_label="Taux d'inflation", source_page=8), PROFILES[doc])
    assert mapping is None and reason == "inflation_window_ambiguous"


def test_cli_report_and_protected_paths(fixture, tmp_path, capsys):
    legacy, target, raw, _ = fixture
    report = tmp_path / "report.json"
    args = ["--legacy", str(legacy), "--target", str(target), "--raw", str(raw)]
    assert migration.main(args + ["--report", str(legacy)]) == 1
    assert migration.main(args + ["--report", str(report)]) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["result"] == "PASS"
    assert "No observations published" in capsys.readouterr().out
