"""Database connections for Trust Core (independent of the legacy MVP store)."""

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, URL, make_url


def create_database_engine(url: str | URL) -> Engine:
    """Enable SQLite foreign keys and real transactional DDL on every connection."""
    if make_url(url).get_backend_name() not in {"sqlite", "postgresql"}:
        raise ValueError("Trust Core supports only SQLite and PostgreSQL")
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _record) -> None:
            # Disable sqlite3's legacy transaction control. SQLAlchemy owns BEGIN,
            # including for DDL, so a failed migration does not leave partial tables.
            connection.isolation_level = None
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            # REPLACE deletes conflicting rows before inserting. SQLite only fires
            # delete guards for that operation when recursive triggers are enabled.
            cursor.execute("PRAGMA recursive_triggers=ON")
            cursor.close()

        @event.listens_for(engine, "begin")
        def begin_sqlite(connection) -> None:
            connection.exec_driver_sql("BEGIN")
    return engine
