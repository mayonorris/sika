"""Trust Core contracts, exercised only against disposable databases."""

from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from sika.database import create_database_engine

ROOT = Path(__file__).resolve().parents[1]
TABLES = {
    "concepts", "geographies", "units", "dimensions", "series", "sources", "releases",
    "observations", "legacy_observation_links", "passages", "quality_runs", "review_decisions",
}
DIMENSIONS = dict(category="all_items", sector="all_sectors", aggregation_level="total",
                  price_basis="not_applicable", seasonal_adjustment="unadjusted", source_variant="none")
NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)


def config_for(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


@pytest.fixture
def database(tmp_path):
    url = sa.URL.create("sqlite", database=str(tmp_path / "trust-core.db"))
    config = config_for(str(url))
    command.upgrade(config, "head")
    engine = create_database_engine(url)
    metadata = sa.MetaData()
    metadata.reflect(engine)
    yield engine, metadata.tables, config
    engine.dispose()


def insert(connection, tables, table, **values):
    return connection.execute(tables[table].insert().values(**values)).inserted_primary_key[0]


def seed(database) -> None:
    engine, tables, _ = database
    with engine.begin() as con:
        insert(con, tables, "concepts", id=1, code="inflation_rate_yoy", label_fr="Inflation annuelle")
        insert(con, tables, "geographies", id=1, code="TG", label_fr="Togo")
        insert(con, tables, "units", id=1, code="percent", label_fr="Pourcentage",
               symbol="%", quantity="rate", scale=1)
        insert(con, tables, "dimensions", id=1, **DIMENSIONS)
        insert(con, tables, "series", id=1, series_key="tg.inflation.yoy",
               concept_id=1, geography_id=1, unit_id=1, dimensions_id=1,
               frequency="monthly", label_fr="Inflation annuelle")
        insert(con, tables, "sources", id=1, source_key="inseed.ihpc", publisher="INSEED",
               title="IHPC", canonical_url="https://example.test/ihpc")
        insert(con, tables, "releases", **release_values())
        insert(con, tables, "observations", **observation_values())


def release_values(**overrides) -> dict:
    return dict(id=1, release_key="inseed.ihpc.2026-06", source_id=1,
                source_doc="inseed_ihpc_2026-06.pdf", source_url="https://example.test/ihpc/june",
                publisher="INSEED", content_sha256="a" * 64, published_on=date(2026, 7, 15),
                retrieved_at=NOW, storage_uri="local:inseed_ihpc_2026-06.pdf", media_type="application/pdf") | overrides


def observation_values(**overrides) -> dict:
    return dict(id=1, series_id=1, release_id=1, period="2026-06", value=0.9,
                source_page=7, original_label="INDICE GLOBAL - Variation 12 mois", confidence=0.95) | overrides


def test_populated_upgrade_downgrade_upgrade(database):
    engine, tables, config = database
    assert set(tables) == TABLES | {"alembic_version"}
    seed(database)
    with engine.begin() as con:
        insert(con, tables, "geographies", id=2, code="TG-M", label_fr="Maritime", parent_id=1)
        insert(con, tables, "releases", **release_values(
            id=2, release_key="revision", content_sha256="b" * 64, supersedes_id=1))
        insert(con, tables, "review_decisions", release_id=1, reviewer="test-reviewer",
               decision="approve", reason="Fixture verified")
        insert(con, tables, "quality_runs", release_id=1, validator_version="test-1", report={})
        insert(con, tables, "passages", release_id=1, source_page=7, text="Fixture text")
        insert(con, tables, "legacy_observation_links", legacy_database="fixture-snapshot",
               legacy_id=22, observation_id=1, original_row={"id": 22})
    # Re-running head must be a no-op, including on a populated schema.
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    assert set(sa.inspect(engine).get_table_names()) == {"alembic_version"}
    with engine.connect() as con:
        assert con.execute(sa.text("SELECT version_num FROM alembic_version")).all() == []
    command.upgrade(config, "head")
    assert set(sa.inspect(engine).get_table_names()) == TABLES | {"alembic_version"}


def test_series_identity_rejects_alias_keys_and_duplicate_dimension_bundles(database):
    seed(database)
    engine, tables, _ = database
    with pytest.raises(IntegrityError), engine.begin() as con:
        insert(con, tables, "dimensions", id=2, **DIMENSIONS)
    with pytest.raises(IntegrityError), engine.begin() as con:
        insert(con, tables, "series", series_key="different-label-same-series",
               concept_id=1, geography_id=1, unit_id=1, dimensions_id=1,
               frequency="monthly", label_fr="Other label")


@pytest.mark.parametrize("dimension", DIMENSIONS)
def test_each_dimension_separates_series_and_cannot_be_omitted(database, dimension):
    seed(database)
    engine, tables, _ = database
    with engine.begin() as con:
        insert(con, tables, "dimensions", id=2, **(DIMENSIONS | {dimension: "source:distinct"}))
        insert(con, tables, "series", series_key="separate-series", concept_id=1,
               geography_id=1, unit_id=1, dimensions_id=2, frequency="monthly", label_fr="Different scope")
    for invalid in (None, ""):
        with pytest.raises(IntegrityError), engine.begin() as con:
            insert(con, tables, "dimensions", **(DIMENSIONS | {dimension: invalid}))


@pytest.mark.parametrize("column", ["concept_id", "geography_id", "unit_id", "dimensions_id"])
def test_series_foreign_keys_are_enforced(database, column):
    seed(database)
    engine, tables, _ = database
    values = dict(series_key="invalid-fk", concept_id=1, geography_id=1, unit_id=1,
                  dimensions_id=1, frequency="annual", label_fr="Fixture")
    with pytest.raises(IntegrityError), engine.begin() as con:
        insert(con, tables, "series", **(values | {column: 999}))


def test_provenance_references_and_vintage_uniqueness(database):
    seed(database)
    engine, tables, _ = database
    with pytest.raises(IntegrityError), engine.begin() as con:
        insert(con, tables, "observations", **observation_values(id=2, release_id=999))
    with pytest.raises(IntegrityError), engine.begin() as con:
        insert(con, tables, "observations", **observation_values(id=2, series_id=999))
    with pytest.raises(IntegrityError), engine.begin() as con:
        con.execute(tables["sources"].delete().where(tables["sources"].c.id == 1))
    with pytest.raises(IntegrityError), engine.begin() as con:
        insert(con, tables, "observations", **observation_values(id=2, source_page=8))
    with engine.begin() as con:
        insert(con, tables, "releases", **release_values(id=2, release_key="later", content_sha256="b" * 64))
        insert(con, tables, "observations", **observation_values(id=2, release_id=2, value=1.0))


@pytest.mark.parametrize("column,value", [
    ("source_doc", "changed.pdf"), ("content_sha256", "b" * 64), ("source_url", "https://changed.test"),
    ("storage_uri", "local:changed.pdf"), ("publisher", "Other publisher"),
    ("published_on", date(2026, 8, 1)), ("retrieved_at", datetime(2026, 9, 20)),
    ("media_type", "text/csv"), ("release_key", "changed"), ("id", 22),
    ("source_id", 2), ("supersedes_id", 2),
])
def test_release_provenance_is_immutable(database, column, value):
    seed(database)
    engine, tables, _ = database
    with engine.begin() as con:
        insert(con, tables, "sources", id=2, source_key="other", publisher="Other", title="Other",
               canonical_url="https://example.test/other")
        insert(con, tables, "releases", **release_values(id=2, release_key="later", content_sha256="b" * 64))
    with pytest.raises(IntegrityError, match="immutable"), engine.begin() as con:
        con.execute(tables["releases"].update().where(tables["releases"].c.id == 1).values({column: value}))


def test_releases_are_retained_and_status_changes_preserve_provenance(database):
    seed(database)
    engine, tables, _ = database
    with engine.begin() as con:
        con.execute(tables["releases"].update().values(status="validating"))
        assert con.execute(sa.select(tables["releases"].c.source_doc)).scalar_one() == "inseed_ihpc_2026-06.pdf"
        # A repeated assignment of an unchanged identity is harmless.
        con.execute(tables["releases"].update().values(source_doc="inseed_ihpc_2026-06.pdf"))
    with pytest.raises(IntegrityError, match="append-only"), engine.begin() as con:
        con.execute(tables["releases"].delete())


def test_metadata_correction_can_reference_identical_document_bytes(database):
    seed(database)
    engine, tables, _ = database
    with engine.begin() as con:
        insert(con, tables, "releases", **release_values(
            id=2, release_key="inseed.ihpc.2026-06.metadata-r1", supersedes_id=1,
            published_on=date(2026, 7, 16)))
    with pytest.raises(IntegrityError), engine.begin() as con:
        insert(con, tables, "releases", **release_values(id=3))


@pytest.mark.parametrize("checksum", ["", "a" * 63, "g" * 64, "A" * 64])
def test_release_checksum_must_be_lowercase_sha256(database, checksum):
    seed(database)
    engine, tables, _ = database
    with pytest.raises(IntegrityError), engine.begin() as con:
        insert(con, tables, "releases", **release_values(id=2, release_key="invalid", content_sha256=checksum))


@pytest.mark.parametrize("table,column,value", [
    ("dimensions", "category", "changed"), ("series", "frequency", "annual"),
    ("series", "series_key", "changed"),
])
def test_series_identity_cannot_drift(database, table, column, value):
    seed(database)
    engine, tables, _ = database
    with pytest.raises(IntegrityError, match="immutable"), engine.begin() as con:
        con.execute(tables[table].update().values({column: value}))


@pytest.mark.parametrize("overrides", [
    {"source_page": 0}, {"source_page": None}, {"confidence": -0.1}, {"confidence": 1.1},
    {"status": "ready"}, {"revision_status": "guessed"}, {"original_label": ""}, {"period": ""},
])
def test_observation_required_provenance_and_states(database, overrides):
    seed(database)
    engine, tables, _ = database
    with pytest.raises(IntegrityError), engine.begin() as con:
        insert(con, tables, "observations", **observation_values(id=2, period="2026-05") | overrides)


def test_legacy_roundtrip_and_draft_defaults(database):
    seed(database)
    engine, tables, _ = database
    original = {"id": 42, "indicator": "inflation_rate_yoy", "indicator_label": "Variation des prix",
                "geography": "Togo", "period": "2026-06", "value": 0.9, "unit": "%",
                "source_doc": "inseed_ihpc_2026-06.pdf", "source_page": 7, "confidence": 0.95}
    with engine.begin() as con:
        insert(con, tables, "legacy_observation_links", legacy_database="snapshot-sha256",
               legacy_id=42, observation_id=1, original_row=original)
        insert(con, tables, "legacy_observation_links", legacy_database="snapshot-sha256",
               legacy_id=43, observation_id=1, original_row=original | {"id": 43})
        assert con.execute(sa.select(tables["legacy_observation_links"].c.original_row)
                           .where(tables["legacy_observation_links"].c.legacy_id == 42)).scalar_one() == original
        for table in ("observations", "releases"):
            assert con.execute(sa.select(tables[table].c.status)).scalar_one() == "draft"
        assert con.execute(sa.select(tables["sources"].c.reuse_status)).scalar_one() == "unknown"
    with pytest.raises(IntegrityError), engine.begin() as con:
        insert(con, tables, "legacy_observation_links", legacy_database="snapshot-sha256",
               legacy_id=42, observation_id=1, original_row=original)


def test_review_decisions_require_one_target_and_are_append_only(database):
    seed(database)
    engine, tables, _ = database
    for targets in ({}, {"release_id": 1, "observation_id": 1}, {"observation_id": 999}):
        with pytest.raises(IntegrityError), engine.begin() as con:
            insert(con, tables, "review_decisions", **targets, reviewer="fixture",
                   decision="approve", reason="Reviewed")
    with engine.begin() as con:
        insert(con, tables, "review_decisions", observation_id=1, reviewer="fixture",
               decision="approve", reason="Reviewed")
    for statement in (tables["review_decisions"].update().values(reason="Changed"),
                      tables["review_decisions"].delete()):
        with pytest.raises(IntegrityError, match="append-only"), engine.begin() as con:
            con.execute(statement)


@pytest.mark.parametrize("table", ["releases", "review_decisions"])
def test_sqlite_replace_cannot_overwrite_append_only_records(database, table):
    seed(database)
    engine, tables, _ = database
    if table == "releases":
        values = release_values(id=2, release_key="unreferenced-release")
        replacement = values | {"source_doc": "changed.pdf"}
    else:
        values = dict(id=2, release_id=1, reviewer="fixture", decision="approve", reason="Reviewed")
        replacement = values | {"reason": "Changed"}
    with engine.begin() as con:
        insert(con, tables, table, **values)
    with pytest.raises(IntegrityError, match="append-only"), engine.begin() as con:
        con.execute(tables[table].insert().prefix_with("OR REPLACE").values(**replacement))


def test_quality_run_cannot_pass_with_hard_failures(database):
    seed(database)
    engine, tables, _ = database
    with pytest.raises(IntegrityError), engine.begin() as con:
        insert(con, tables, "quality_runs", release_id=1, validator_version="test-1",
               status="passed", started_at=NOW, completed_at=NOW, hard_failure_count=1, report={})
    with engine.begin() as con:
        insert(con, tables, "quality_runs", release_id=1, validator_version="test-1",
               status="passed", started_at=NOW, completed_at=NOW, report={"checks": []})


def test_migration_refuses_legacy_database_without_modifying_it(tmp_path):
    path = tmp_path / "legacy.db"
    url = sa.URL.create("sqlite", database=str(path))
    engine = create_database_engine(url)
    with engine.begin() as con:
        con.exec_driver_sql("CREATE TABLE observations (id INTEGER PRIMARY KEY, value REAL)")
        con.exec_driver_sql("INSERT INTO observations VALUES (1, 0.9)")
    engine.dispose()
    original = path.read_bytes()
    with pytest.raises(RuntimeError, match="unversioned"):
        command.upgrade(config_for(str(url)), "head")
    assert path.read_bytes() == original


def test_missing_database_url_is_rejected(monkeypatch):
    monkeypatch.delenv("SIKA_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="SIKA_DATABASE_URL"):
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


def test_failed_schema_transaction_leaves_no_partial_tables(tmp_path):
    engine = create_database_engine(sa.URL.create("sqlite", database=str(tmp_path / "failed.db")))
    try:
        with pytest.raises(RuntimeError), engine.begin() as con:
            con.exec_driver_sql("CREATE TABLE partial (id INTEGER PRIMARY KEY)")
            raise RuntimeError("Simulate a failed DDL migration")
        assert sa.inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_postgresql_migration_sql_renders_without_a_server():
    config = config_for("postgresql://unused/unused")
    config.output_buffer = StringIO()
    command.upgrade(config, "head", sql=True)
    sql = config.output_buffer.getvalue()
    assert "CREATE TABLE observations" in sql
    assert "CONSTRAINT uq_series_identity UNIQUE" in sql
    assert "IS DISTINCT FROM" in sql
    assert "CREATE TRIGGER trg_releases_immutable" in sql
    config.output_buffer = StringIO()
    command.downgrade(config, "head:base", sql=True)
    rollback = config.output_buffer.getvalue()
    assert "DROP TABLE releases" in rollback
    assert "DROP FUNCTION guard_releases_identity()" in rollback
