"""Database connections for Trust Core (independent of the legacy MVP store)."""

from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.engine import Connection


@contextmanager
def _allow_writes(connection: Connection, *permissions: tuple[str, str, int]) -> Iterator[None]:
    """Private service capability, not a SQL-settable flag or user-supplied claim."""
    previous = connection.info.get("sika_write_permissions", frozenset())
    connection.info["sika_write_permissions"] = frozenset(permissions)
    try:
        yield
    finally:
        connection.info["sika_write_permissions"] = previous


def create_database_engine(url: str | URL, *, sqlite_begin: str = "deferred") -> Engine:
    """Enable SQLite foreign keys and real transactional DDL on every connection."""
    if type(sqlite_begin) is not str or sqlite_begin not in {"deferred", "immediate"}:
        raise ValueError("Unsupported SQLite transaction mode")
    if make_url(url).get_backend_name() not in {"sqlite", "postgresql"}:
        raise ValueError("Trust Core supports only SQLite and PostgreSQL")
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, record) -> None:
            # Disable sqlite3's legacy transaction control. SQLAlchemy owns BEGIN,
            # including for DDL, so a failed migration does not leave partial tables.
            connection.isolation_level = None
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            # REPLACE deletes conflicting rows before inserting. SQLite only fires
            # delete guards for that operation when recursive triggers are enabled.
            cursor.execute("PRAGMA recursive_triggers=ON")
            cursor.close()
            record.info["sika_write_permissions"] = frozenset()
            connection.create_function("sika_write_allowed", 3, lambda table, operation, row_id:
                int((table, operation, row_id) in record.info.get("sika_write_permissions", ())))

        @event.listens_for(engine, "checkin")
        def reset_permissions(_connection, record) -> None:
            record.info["sika_write_permissions"] = frozenset()

        @event.listens_for(engine, "begin")
        def begin_sqlite(connection) -> None:
            connection.exec_driver_sql("BEGIN IMMEDIATE" if sqlite_begin == "immediate" else "BEGIN")
    return engine
