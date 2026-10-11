"""Protect completed evidence and local service-owned publication transitions."""

from alembic import op
import sqlalchemy as sa

revision = "0003_publication_gate"
down_revision = "0002_inflation_audit"
branch_labels = None
depends_on = None

PROTECTED = "('reviewed','published','corrected','superseded')"
FACTS = ("id", "series_id", "release_id", "period", "value", "source_page", "source_locator",
         "original_label", "confidence", "revision_status", "created_at")


def _checksum(column: str) -> sa.CheckConstraint:
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return sa.CheckConstraint(f"length({column})=64 AND length({remainder})=0", name=f"ck_event_{column}")


def _guards(dialect: str) -> list[tuple[str, str, str, str, str]]:
    """SQL constants only. PostgreSQL sensitive writes stay unavailable by design."""
    denied = lambda table, operation, identifier: (
        f"sika_write_allowed('{table}','{operation}',{identifier}) <> 1" if dialect == "sqlite" else "TRUE")
    differs = "IS NOT" if dialect == "sqlite" else "IS DISTINCT FROM"
    protected_release = lambda prefix: f"EXISTS (SELECT 1 FROM releases r WHERE r.id={prefix}.release_id AND r.status IN {PROTECTED})"
    data_changed = " OR ".join(f"OLD.{c} {differs} NEW.{c}" for c in FACTS)
    checks = [
        ("quality_runs", "collision", "INSERT", "EXISTS(SELECT 1 FROM quality_runs q WHERE q.id=NEW.id)", "quality evidence is append-only"),
        ("quality_runs", "completion", "INSERT", f"NEW.status <> 'running' AND ({denied('quality_runs', 'insert', -1)})", "quality completion requires the service"),
        ("quality_runs", "immutable", "UPDATE", f"OLD.status <> 'running' OR ({denied('quality_runs', 'update', 'OLD.id')})", "completed quality evidence is immutable"),
        ("quality_runs", "retained", "DELETE", "TRUE", "quality evidence is append-only"),
        ("review_decisions", "collision", "INSERT", "EXISTS(SELECT 1 FROM review_decisions d WHERE d.id=NEW.id)", "review history is append-only"),
        ("review_decisions", "chronology", "INSERT", "NEW.id <= 0 OR EXISTS(SELECT 1 FROM review_decisions d WHERE d.id > NEW.id)", "review identifiers must increase monotonically"),
        ("releases", "collision", "INSERT", "EXISTS(SELECT 1 FROM releases r WHERE r.id=NEW.id OR r.release_key=NEW.release_key)", "release provenance is append-only"),
        ("publication_events", "collision", "INSERT", "EXISTS(SELECT 1 FROM publication_events e WHERE e.id=NEW.id OR e.review_decision_id=NEW.review_decision_id)", "publication evidence is append-only"),
        ("publication_events", "authorized", "INSERT", denied("publication_events", "insert", -1), "publication evidence requires the service"),
        ("publication_events", "coherent", "INSERT", "NOT EXISTS (SELECT 1 FROM quality_runs q JOIN review_decisions d ON d.id=NEW.review_decision_id WHERE q.id=NEW.quality_run_id AND q.release_id=NEW.release_id AND q.status='passed' AND q.hard_failure_count=0 AND d.release_id=NEW.release_id AND d.observation_id IS NULL AND d.decision=NEW.action)", "publication proof targets do not agree"),
        ("publication_events", "immutable", "UPDATE", "TRUE", "publication evidence is append-only"),
        ("publication_events", "retained", "DELETE", "TRUE", "publication evidence is append-only"),
    ]
    for table in ("releases", "observations"):
        checks.extend([
            (table, "initial_state", "INSERT", f"NEW.status IN {PROTECTED}", "protected states require review transitions"),
            (table, "transition", "UPDATE", f"OLD.status {differs} NEW.status AND (OLD.status IN {PROTECTED} OR NEW.status IN {PROTECTED}) AND ({denied(table, 'transition', 'OLD.id')})", "protected transition requires the service"),
        ])
    checks.extend([
        ("observations", "cohort_insert", "INSERT", protected_release("NEW"), "reviewed cohort is immutable"),
        ("observations", "cohort_attach", "UPDATE", f"OLD.release_id {differs} NEW.release_id AND {protected_release('NEW')}", "reviewed cohort is immutable"),
        ("observations", "collision", "INSERT", f"EXISTS(SELECT 1 FROM observations o WHERE o.status IN {PROTECTED} AND (o.id=NEW.id OR (o.series_id=NEW.series_id AND o.period=NEW.period AND o.release_id=NEW.release_id)))", "reviewed observations are immutable"),
        ("observations", "update_collision", "UPDATE", f"EXISTS(SELECT 1 FROM observations o WHERE o.id <> OLD.id AND o.status IN {PROTECTED} AND (o.id=NEW.id OR (o.series_id=NEW.series_id AND o.period=NEW.period AND o.release_id=NEW.release_id)))", "reviewed observations are immutable"),
        ("observations", "facts", "UPDATE", f"OLD.status IN {PROTECTED} AND ({data_changed})", "reviewed observations are immutable"),
        ("observations", "retained", "DELETE", f"OLD.status IN {PROTECTED}", "reviewed observations are immutable"),
        ("passages", "cohort_insert", "INSERT", protected_release("NEW"), "reviewed passages are immutable"),
        ("passages", "collision", "INSERT", f"EXISTS(SELECT 1 FROM passages p JOIN releases r ON r.id=p.release_id WHERE r.status IN {PROTECTED} AND (p.id=NEW.id OR (p.release_id=NEW.release_id AND p.source_page=NEW.source_page) OR (p.legacy_database=NEW.legacy_database AND p.legacy_id=NEW.legacy_id)))", "reviewed passages are immutable"),
        ("passages", "update_collision", "UPDATE", f"EXISTS(SELECT 1 FROM passages p JOIN releases r ON r.id=p.release_id WHERE p.id <> OLD.id AND r.status IN {PROTECTED} AND (p.id=NEW.id OR (p.release_id=NEW.release_id AND p.source_page=NEW.source_page) OR (p.legacy_database=NEW.legacy_database AND p.legacy_id=NEW.legacy_id)))", "reviewed passages are immutable"),
        ("passages", "cohort_update", "UPDATE", f"{protected_release('OLD')} OR {protected_release('NEW')}", "reviewed passages are immutable"),
        ("passages", "cohort_delete", "DELETE", protected_release("OLD"), "reviewed passages are immutable"),
    ])
    return checks


def _preflight() -> None:
    query = f"SELECT COUNT(*) FROM (SELECT id FROM releases WHERE status IN {PROTECTED} UNION ALL SELECT id FROM observations WHERE status IN {PROTECTED}) AS unsupported"
    if op.get_context().as_sql:
        # PostgreSQL offline SQL must also fail closed when applied to populated data.
        if op.get_context().dialect.name == "postgresql":
            op.execute(f"DO $$ BEGIN IF ({query}) > 0 THEN RAISE EXCEPTION 'Unsupported pre-existing publication states'; END IF; END $$")
    elif op.get_bind().execute(sa.text(query)).scalar_one():
        raise RuntimeError("Unsupported pre-existing publication states; no trust can be synthesized")


def upgrade() -> None:
    _preflight()
    op.create_table(
        "publication_events", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("release_id", sa.Integer, sa.ForeignKey("releases.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quality_run_id", sa.Integer, sa.ForeignKey("quality_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("review_decision_id", sa.Integer, sa.ForeignKey("review_decisions.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("before_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("after_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("evidence", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.CheckConstraint("action IN ('approve','publish')", name="ck_event_action"),
        _checksum("before_snapshot_sha256"), _checksum("after_snapshot_sha256"),
    )
    op.create_index("ix_publication_events_release", "publication_events", ["release_id", "id"])
    dialect = op.get_context().dialect.name
    for table, key, operation, condition, message in _guards(dialect):
        name = f"trg_gate_{table}_{key}"
        if dialect == "sqlite":
            # SQLite assigns an automatic row ID only after BEFORE INSERT. Checking
            # chronology afterwards also rejects explicitly supplied negative IDs.
            timing = "AFTER" if key == "chronology" else "BEFORE"
            op.execute(f"CREATE TRIGGER {name} {timing} {operation} ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT, '{message}'); END")
        else:
            # No SQL-settable context flag: a least-privilege PG adapter is deferred.
            op.execute(f"CREATE FUNCTION fn_{name}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {condition} THEN RAISE EXCEPTION '{message}'; END IF; IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW; END; $$")
            op.execute(f"CREATE TRIGGER {name} BEFORE {operation} ON {table} FOR EACH ROW EXECUTE FUNCTION fn_{name}()")


def downgrade() -> None:
    if op.get_context().as_sql:
        if op.get_context().dialect.name == "postgresql":
            op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM publication_events) THEN RAISE EXCEPTION 'Archive publication evidence before rollback on a disposable restore'; END IF; END $$")
    elif op.get_bind().execute(sa.text("SELECT COUNT(*) FROM publication_events")).scalar_one():
        raise RuntimeError("Publication audit exists; refuse destructive rollback")
    dialect = op.get_context().dialect.name
    for table, key, _operation, _condition, _message in reversed(_guards(dialect)):
        name = f"trg_gate_{table}_{key}"
        op.execute(f"DROP TRIGGER {name}" + (f" ON {table}" if dialect == "postgresql" else ""))
        if dialect == "postgresql":
            op.execute(f"DROP FUNCTION fn_{name}()")
    op.drop_table("publication_events")
