"""Preserve every legacy inflation row, including unresolved mappings.

Revision ID: 0002_inflation_audit
Revises: 0001_trust_core
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_inflation_audit"
down_revision = "0001_trust_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inflation_migration_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("legacy_database", sa.String(200), nullable=False, unique=True),
        sa.Column("mapping_version", sa.String(100), nullable=False),
        sa.Column("source_manifest", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.current_timestamp()),
    )
    for table, target in (
        ("inflation_migration_rows", "observations"),
        ("inflation_migration_passages", "passages"),
    ):
        op.create_table(
            table,
            sa.Column("run_id", sa.Integer, sa.ForeignKey("inflation_migration_runs.id", ondelete="RESTRICT"), primary_key=True),
            sa.Column("legacy_id", sa.Integer, primary_key=True),
            sa.Column("target_id", sa.Integer, sa.ForeignKey(f"{target}.id", ondelete="RESTRICT")),
            sa.Column("source_doc", sa.Text, nullable=False),
            sa.Column("source_page", sa.Integer),
            sa.Column("disposition", sa.String(20), nullable=False),
            sa.Column("reason", sa.Text, nullable=False),
            sa.Column("original_row", sa.JSON, nullable=False),
            sa.CheckConstraint("(disposition IN ('mapped', 'merged') AND target_id IS NOT NULL) OR "
                               "(disposition = 'quarantined' AND target_id IS NULL)", name=f"ck_{table}_target"),
        )
        op.create_index(f"ix_{table}_target", table, ["target_id"])
        op.create_index(f"ix_{table}_disposition", table, ["run_id", "disposition"])


def downgrade() -> None:
    op.drop_table("inflation_migration_passages")
    op.drop_table("inflation_migration_rows")
    op.drop_table("inflation_migration_runs")
