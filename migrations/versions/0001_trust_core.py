"""Create the canonical statistical schema beside the legacy store.

Revision ID: 0001_trust_core
Revises: None
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_trust_core"
down_revision = None
branch_labels = None
depends_on = None

DIMENSION_FIELDS = (
    "category", "sector", "aggregation_level", "price_basis",
    "seasonal_adjustment", "source_variant",
)
SERIES_IDENTITY = ("concept_id", "geography_id", "unit_id", "frequency", "dimensions_id")
RELEASE_PROVENANCE = (
    "id", "release_key", "source_id", "source_doc", "source_url", "publisher",
    "content_sha256", "published_on", "retrieved_at", "storage_uri", "media_type",
    "supersedes_id",
)
IMMUTABLE = {
    "dimensions": ("id", *DIMENSION_FIELDS),
    "series": ("id", "series_key", *SERIES_IDENTITY),
    "releases": RELEASE_PROVENANCE,
}


def _id() -> sa.Column:
    return sa.Column("id", sa.Integer, primary_key=True)


def _created() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                     server_default=sa.func.current_timestamp())


def _checksum_check() -> sa.CheckConstraint:
    remainder = "content_sha256"
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return sa.CheckConstraint(
        f"length(content_sha256) = 64 AND length({remainder}) = 0",
        name="ck_releases_checksum",
    )


def _fk(name: str, target: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.Integer, sa.ForeignKey(
        target, ondelete="RESTRICT", name=f"fk_{name}_{target.split('.')[0]}"),
                     nullable=nullable)


def _nonempty(table: str, *columns: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        " AND ".join(f"length(trim({column})) > 0" for column in columns),
        name=f"ck_{table}_required_text",
    )


def _immutability_triggers() -> None:
    dialect = op.get_context().dialect.name
    for table, columns in IMMUTABLE.items():
        if dialect == "sqlite":
            condition = " OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in columns)
            op.execute(f"""CREATE TRIGGER trg_{table}_immutable
                BEFORE UPDATE ON {table} WHEN {condition}
                BEGIN SELECT RAISE(ABORT, '{table} identity is immutable'); END""")
        else:
            condition = " OR ".join(f"OLD.{c} IS DISTINCT FROM NEW.{c}" for c in columns)
            op.execute(f"""CREATE FUNCTION guard_{table}_identity() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                IF {condition} THEN
                    RAISE EXCEPTION '{table} identity is immutable';
                END IF;
                RETURN NEW;
                END; $$""")
            op.execute(f"""CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE ON {table}
                FOR EACH ROW EXECUTE FUNCTION guard_{table}_identity()""")
    # Provenance and audit decisions are retained even before they have dependants.
    if dialect == "sqlite":
        for table, operation in (("releases", "DELETE"), ("review_decisions", "UPDATE"),
                                 ("review_decisions", "DELETE")):
            op.execute(f"""CREATE TRIGGER trg_{table}_no_{operation.lower()}
                BEFORE {operation} ON {table}
                BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END""")
    else:
        op.execute("""CREATE FUNCTION guard_append_only() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END; $$""")
        op.execute("""CREATE TRIGGER trg_releases_no_delete BEFORE DELETE ON releases
            FOR EACH ROW EXECUTE FUNCTION guard_append_only()""")
        op.execute("""CREATE TRIGGER trg_review_decisions_append_only
            BEFORE UPDATE OR DELETE ON review_decisions
            FOR EACH ROW EXECUTE FUNCTION guard_append_only()""")


def upgrade() -> None:
    if op.get_context().dialect.name not in {"sqlite", "postgresql"}:
        raise RuntimeError("Trust Core supports only SQLite and PostgreSQL")
    op.create_table(
        "concepts", _id(),
        sa.Column("code", sa.String(160), nullable=False, unique=True),
        sa.Column("label_fr", sa.Text, nullable=False),
        sa.Column("label_en", sa.Text), sa.Column("description", sa.Text), _created(),
        _nonempty("concepts", "code", "label_fr"),
    )
    op.create_table(
        "geographies", _id(),
        sa.Column("code", sa.String(80), nullable=False, unique=True),
        sa.Column("label_fr", sa.Text, nullable=False), sa.Column("label_en", sa.Text),
        _fk("parent_id", "geographies.id", nullable=True),
        _nonempty("geographies", "code", "label_fr"),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_geography_parent"),
    )
    op.create_index("ix_geographies_parent", "geographies", ["parent_id"])
    op.create_table(
        "units", _id(), sa.Column("code", sa.String(80), nullable=False, unique=True),
        sa.Column("label_fr", sa.Text, nullable=False), sa.Column("label_en", sa.Text),
        sa.Column("symbol", sa.String(80), nullable=False),
        sa.Column("quantity", sa.String(80), nullable=False),
        sa.Column("scale", sa.Numeric(28, 10), nullable=False),
        sa.CheckConstraint("scale > 0", name="ck_units_positive_scale"),
        _nonempty("units", "code", "label_fr", "symbol", "quantity"),
    )
    op.create_table(
        "dimensions", _id(),
        *(sa.Column(field, sa.String(200), nullable=False) for field in DIMENSION_FIELDS),
        sa.Column("description", sa.Text),
        sa.UniqueConstraint(*DIMENSION_FIELDS, name="uq_dimensions_identity"),
        _nonempty("dimensions", *DIMENSION_FIELDS),
    )
    op.create_table(
        "series", _id(), sa.Column("series_key", sa.String(200), nullable=False, unique=True),
        _fk("concept_id", "concepts.id"), _fk("geography_id", "geographies.id"),
        _fk("unit_id", "units.id"), _fk("dimensions_id", "dimensions.id"),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("label_fr", sa.Text, nullable=False), sa.Column("label_en", sa.Text), _created(),
        sa.CheckConstraint("frequency IN ('annual', 'quarterly', 'monthly', 'daily', 'irregular')",
                           name="ck_series_frequency"),
        sa.UniqueConstraint(*SERIES_IDENTITY, name="uq_series_identity"),
        _nonempty("series", "series_key", "label_fr"),
    )
    for field in ("geography_id", "unit_id", "dimensions_id"):
        op.create_index(f"ix_series_{field}", "series", [field])
    op.create_table(
        "sources", _id(), sa.Column("source_key", sa.String(200), nullable=False, unique=True),
        sa.Column("publisher", sa.Text, nullable=False), sa.Column("title", sa.Text, nullable=False),
        sa.Column("canonical_url", sa.Text, nullable=False),
        sa.Column("license_url", sa.Text), sa.Column("attribution", sa.Text),
        sa.Column("reuse_status", sa.String(24), nullable=False, server_default="unknown"),
        _created(), _nonempty("sources", "source_key", "publisher", "title", "canonical_url"),
        sa.CheckConstraint("reuse_status IN ('unknown', 'allowed', 'restricted', 'prohibited')",
                           name="ck_sources_reuse_status"),
    )
    op.create_table(
        "releases", _id(), sa.Column("release_key", sa.String(200), nullable=False, unique=True),
        _fk("source_id", "sources.id"), sa.Column("source_doc", sa.Text, nullable=False),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("publisher", sa.Text, nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("published_on", sa.Date),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("storage_uri", sa.Text, nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        _fk("supersedes_id", "releases.id", nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"), _created(),
        _checksum_check(),
        sa.CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="ck_release_supersedes"),
        sa.CheckConstraint("status IN ('discovered', 'extracting', 'draft', 'validating', "
                           "'reviewed', 'published', 'quarantined', 'corrected', 'superseded')",
                           name="ck_releases_status"),
        _nonempty("releases", "release_key", "source_doc", "source_url", "publisher", "storage_uri", "media_type"),
    )
    op.create_index("ix_releases_status_date", "releases", ["status", "published_on"])
    # Identical bytes may be reissued as a distinct vintage or metadata correction.
    # Idempotency uses release_key; the checksum is a content lookup, not an identity.
    op.create_index("ix_releases_content", "releases", ["source_id", "content_sha256"])
    op.create_index("ix_releases_supersedes", "releases", ["supersedes_id"])
    op.create_index("ix_releases_source_doc", "releases", ["source_doc"])
    op.create_table(
        "observations", _id(), _fk("series_id", "series.id"), _fk("release_id", "releases.id"),
        sa.Column("period", sa.String(32), nullable=False),
        sa.Column("value", sa.Numeric(28, 10), nullable=False),
        sa.Column("source_page", sa.Integer, nullable=False),
        sa.Column("source_locator", sa.Text),
        sa.Column("original_label", sa.Text, nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("revision_status", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"), _created(),
        sa.UniqueConstraint("series_id", "period", "release_id", name="uq_observations_vintage"),
        sa.CheckConstraint("source_page >= 1", name="ck_observations_page"),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
                           name="ck_observations_confidence"),
        sa.CheckConstraint("revision_status IN ('unknown', 'provisional', 'revised', 'final')",
                           name="ck_observations_revision_status"),
        sa.CheckConstraint("status IN ('draft', 'reviewed', 'published', 'quarantined', 'corrected', 'superseded')",
                           name="ck_observations_status"),
        _nonempty("observations", "period", "original_label"),
    )
    op.create_index("ix_observations_public_series", "observations", ["series_id", "status", "period"])
    op.create_index("ix_observations_release_page", "observations", ["release_id", "source_page"])
    op.create_table(
        "legacy_observation_links",
        sa.Column("legacy_database", sa.String(200), primary_key=True),
        sa.Column("legacy_id", sa.Integer, primary_key=True),
        _fk("observation_id", "observations.id"),
        sa.Column("original_row", sa.JSON, nullable=False),
        _nonempty("legacy_observation_links", "legacy_database"),
    )
    op.create_index("ix_legacy_links_observation", "legacy_observation_links", ["observation_id"])
    op.create_table(
        "passages", _id(), _fk("release_id", "releases.id"),
        sa.Column("source_page", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("legacy_database", sa.String(200)), sa.Column("legacy_id", sa.Integer),
        sa.UniqueConstraint("release_id", "source_page", name="uq_passages_page"),
        sa.UniqueConstraint("legacy_database", "legacy_id", name="uq_passages_legacy"),
        sa.CheckConstraint("source_page >= 1", name="ck_passages_page"),
        sa.CheckConstraint("(legacy_database IS NULL AND legacy_id IS NULL) OR "
                           "(legacy_database IS NOT NULL AND legacy_id IS NOT NULL)", name="ck_passages_legacy_pair"),
    )
    op.create_table(
        "quality_runs", _id(), _fk("release_id", "releases.id"),
        sa.Column("validator_version", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("hard_failure_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("report", sa.JSON, nullable=False),
        sa.CheckConstraint("status IN ('running', 'passed', 'failed')", name="ck_quality_runs_status"),
        sa.CheckConstraint("hard_failure_count >= 0 AND warning_count >= 0", name="ck_quality_runs_counts"),
        sa.CheckConstraint("status <> 'passed' OR hard_failure_count = 0", name="ck_quality_runs_passed"),
        sa.CheckConstraint("(status = 'running' AND completed_at IS NULL) OR "
                           "(status <> 'running' AND completed_at IS NOT NULL AND completed_at >= started_at)",
                           name="ck_quality_runs_completion"),
        _nonempty("quality_runs", "validator_version"),
    )
    op.create_index("ix_quality_runs_release", "quality_runs", ["release_id", "started_at"])
    op.create_table(
        "review_decisions", _id(), _fk("release_id", "releases.id", nullable=True),
        _fk("observation_id", "observations.id", nullable=True),
        sa.Column("reviewer", sa.String(200), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text, nullable=False), _created(),
        sa.CheckConstraint("(release_id IS NOT NULL AND observation_id IS NULL) OR "
                           "(release_id IS NULL AND observation_id IS NOT NULL)", name="ck_review_one_target"),
        sa.CheckConstraint("decision IN ('approve', 'reject', 'quarantine', 'correct', 'publish', 'supersede')",
                           name="ck_review_decision"),
        _nonempty("review_decisions", "reviewer", "reason"),
    )
    op.create_index("ix_review_release", "review_decisions", ["release_id", "created_at"])
    op.create_index("ix_review_observation", "review_decisions", ["observation_id", "created_at"])
    _immutability_triggers()


def downgrade() -> None:
    # Reverse dependency order. DROP removes each table's triggers automatically.
    if op.get_context().dialect.name == "sqlite":
        op.execute("DROP TRIGGER trg_releases_immutable")
        op.execute("UPDATE releases SET supersedes_id = NULL")
    for table in ("review_decisions", "quality_runs", "passages", "legacy_observation_links",
                  "observations", "releases", "sources", "series", "dimensions", "units"):
        op.drop_table(table)
    # SQLite enforces self-references during DROP when hierarchy rows exist.
    op.execute("UPDATE geographies SET parent_id = NULL")
    op.drop_table("geographies")
    op.drop_table("concepts")
    if op.get_context().dialect.name == "postgresql":
        for table in IMMUTABLE:
            op.execute(f"DROP FUNCTION guard_{table}_identity()")
        op.execute("DROP FUNCTION guard_append_only()")
