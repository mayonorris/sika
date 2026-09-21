"""Run only explicitly targeted Trust Core migrations; never load legacy .env."""

import os

import sqlalchemy as sa
from alembic import context

from sika.database import create_database_engine

config = context.config
# Programmatic tests may supply a URL without environment-variable interference.
url = config.get_main_option("sqlalchemy.url") or os.environ.get("SIKA_DATABASE_URL")
if not url:
    raise RuntimeError("Set SIKA_DATABASE_URL to an explicit Trust Core database URL")


def run_online() -> None:
    engine = create_database_engine(url)
    try:
        with engine.begin() as connection:
            tables = set(sa.inspect(connection).get_table_names())
            versioned = "alembic_version" in tables and connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).first() is not None
            if tables - {"alembic_version"} and not versioned:
                raise RuntimeError(
                    "Refusing an existing unversioned database; use a separate empty "
                    "Trust Core database, never the legacy sika.db"
                )
            context.configure(connection=connection, transactional_ddl=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    context.configure(url=url, literal_binds=True, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    run_online()
