"""Adversarial SQL-DML and rollback cases for the local publication boundary."""

import importlib
import sqlite3

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from sika import publication, quality
from test_publication import authorized, ready, approve_ready, approval_id, state
from test_quality import ROOT, store


@pytest.mark.parametrize("stage", ["reviewed", "published"])
@pytest.mark.parametrize("review_id", [-1, 0, 1])
def test_later_review_cannot_hide_behind_a_lower_id(ready, stage, review_id):
    target, engine, tables, _ = ready
    # Leave genuine unused positive IDs below the authoritative review's ID.
    with engine.begin() as con:
        con.execute(tables["review_decisions"].insert().values(
            id=10, release_id=1, reviewer="test:reviewer", decision="reject",
            reason="Unbound history for an unrelated draft vintage."))
    approval, _, _ = approve_ready(ready)
    if stage == "published":
        publication.publish(target, 2, approval_id(approval), "Publish synthetic fixture.")
    before = state(ready)
    with pytest.raises(DBAPIError), engine.begin() as con:
        con.execute(tables["review_decisions"].insert().values(
            id=review_id, release_id=2, reviewer="test:reviewer", decision="reject",
            reason="A newly recorded rejection must not receive a retrograde ID."))
    assert state(ready) == before
    if stage == "reviewed":
        publication.publish(target, 2, approval_id(approval), "Publish unchanged fixture.")
    assert len(publication.published_rows(target, 2)) == 1


@pytest.mark.parametrize("table", ["observations", "passages"])
@pytest.mark.parametrize("stage", ["reviewed", "published"])
def test_update_replace_cannot_delete_a_protected_id_collision(ready, table, stage):
    target, engine, tables, _ = ready
    if table == "passages":
        with engine.begin() as con:
            con.execute(tables["passages"].insert(), [
                dict(id=1, release_id=1, source_page=1, text="Draft synthetic passage."),
                dict(id=2, release_id=2, source_page=1, text="Reviewed synthetic passage."),
            ])
    approval, _, _ = approve_ready(ready)
    if stage == "published":
        publication.publish(target, 2, approval_id(approval), "Publish synthetic fixture.")
    with engine.connect() as con:
        before = con.execute(sa.select(tables[table]).order_by(tables[table].c.id)).mappings().all()
    with pytest.raises(DBAPIError), engine.begin() as con:
        con.exec_driver_sql("PRAGMA recursive_triggers=OFF")
        con.exec_driver_sql(f"UPDATE OR REPLACE {table} SET id=2 WHERE id=1")
    with engine.connect() as con:
        after = con.execute(sa.select(tables[table]).order_by(tables[table].c.id)).mappings().all()
    assert after == before
    if stage == "published":
        assert len(publication.published_rows(target, 2)) == 1


@pytest.mark.parametrize("operation", ["insert", "update"])
def test_replace_cannot_delete_a_protected_passage_via_its_legacy_key(operation):
    """Seed before installing the real guards, as a trusted schema fixture only.

    A legacy-key conflict is independent of the ID and release/page conflict keys.
    This small fixture deliberately does not claim source/publication certification.
    """
    migration = importlib.import_module("migrations.versions.0003_publication_gate")
    with sqlite3.connect(":memory:") as con:
        con.execute("CREATE TABLE releases(id INTEGER PRIMARY KEY, status TEXT)")
        con.execute("CREATE TABLE passages(id INTEGER PRIMARY KEY, release_id INTEGER, "
                    "source_page INTEGER, text TEXT, legacy_database TEXT, legacy_id INTEGER, "
                    "UNIQUE(release_id, source_page), UNIQUE(legacy_database, legacy_id))")
        con.execute("INSERT INTO releases VALUES(1,'draft'),(2,'reviewed')")
        con.execute("INSERT INTO passages VALUES(1,1,1,'Draft','legacy-snapshot',41),"
                    "(2,2,1,'Protected','legacy-snapshot',42)")
        for table, key, action, condition, _message in migration._guards("sqlite"):
            if table == "passages":
                con.execute(f"CREATE TRIGGER fixture_{key} BEFORE {action} ON passages "
                            f"WHEN {condition} BEGIN SELECT RAISE(ABORT, 'protected'); END")
        con.execute("PRAGMA recursive_triggers=OFF")
        before = con.execute("SELECT * FROM passages ORDER BY id").fetchall()
        with pytest.raises(sqlite3.DatabaseError):
            if operation == "insert":
                con.execute("INSERT OR REPLACE INTO passages VALUES"
                            "(3,1,2,'Replacement','legacy-snapshot',42)")
            else:
                con.execute("UPDATE OR REPLACE passages SET legacy_id=42 WHERE id=1")
        assert con.execute("SELECT * FROM passages ORDER BY id").fetchall() == before


@pytest.mark.parametrize("stage", ["reviewed", "published"])
def test_populated_publication_audit_refuses_downgrade_without_losing_evidence(ready, stage):
    target, engine, tables, _ = ready
    approval, _, _ = approve_ready(ready)
    if stage == "published":
        publication.publish(target, 2, approval_id(approval), "Publish synthetic fixture.")
    before = state(ready)
    with engine.connect() as con:
        events = con.execute(sa.select(tables["publication_events"])
                             .order_by(tables["publication_events"].c.id)).mappings().all()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", str(sa.URL.create(
        "sqlite", database=str(target))).replace("%", "%%"))
    with pytest.raises(RuntimeError, match="Publication audit exists"):
        command.downgrade(config, "0002_inflation_audit")
    assert state(ready) == before
    assert "publication_events" in sa.inspect(engine).get_table_names()
    with engine.connect() as con:
        assert con.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == quality.SCHEMA_VERSION
        assert con.execute(sa.select(tables["publication_events"])
                           .order_by(tables["publication_events"].c.id)).mappings().all() == events
