"""Trend sources, evidence, snapshots, and content opportunities.

Revision ID: 006_trend_intelligence
Revises: 005_trend_research
Create Date: 2026-09-24

Fresh databases created from current models already include these tables.
This revision adds them when an older database stopped at 005_trend_research.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "006_trend_intelligence"
down_revision = "005_trend_research"
branch_labels = None
depends_on = None

_NEW_TABLES = (
    "trend_sources",
    "trend_evidence",
    "account_snapshots",
    "media_snapshots",
    "insight_snapshots",
    "content_opportunities",
)

_OBSERVATION_COLUMNS = {
    "source_id": sa.Column("source_id", sa.String(36), nullable=True),
    "description": sa.Column("description", sa.Text(), nullable=False, server_default=""),
    "trend_type": sa.Column("trend_type", sa.String(64), nullable=True),
    "keywords": sa.Column("keywords", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    "external_id": sa.Column("external_id", sa.String(128), nullable=True),
    "published_at": sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    "valid_until": sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
    "confidence": sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
    "status": sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
    "updated_at": sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
}

_REPORT_COLUMNS = {
    "business_id": sa.Column("business_id", sa.String(36), nullable=True),
    "valid_until": sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
    "status": sa.Column("status", sa.String(32), nullable=False, server_default="READY"),
    "payload": sa.Column("payload", sa.JSON(), nullable=True),
}

_INDEXES = (
    ("trend_sources", "ix_trend_sources_industry", ["industry"]),
    ("trend_sources", "ix_trend_sources_region", ["region"]),
    ("trend_observations", "ix_trend_observations_observed_at", ["observed_at"]),
    ("trend_observations", "ix_trend_observations_valid_until", ["valid_until"]),
    ("trend_observations", "ix_trend_observations_industry", ["industry"]),
    ("trend_observations", "ix_trend_observations_region", ["region"]),
    ("trend_observations", "ix_trend_observations_trend_type", ["trend_type"]),
    ("trend_observations", "ix_trend_observations_status", ["status"]),
    (
        "trend_observations",
        "ix_trend_observations_lookup",
        ["industry", "region", "trend_type", "status", "observed_at"],
    ),
    ("trend_evidence", "ix_trend_evidence_observed_at", ["observed_at"]),
    ("trend_evidence", "ix_trend_evidence_valid_until", ["valid_until"]),
    ("account_snapshots", "ix_account_snapshots_business_id", ["business_id"]),
    ("account_snapshots", "ix_account_snapshots_observed_at", ["observed_at"]),
    ("media_snapshots", "ix_media_snapshots_business_id", ["business_id"]),
    ("media_snapshots", "ix_media_snapshots_observed_at", ["observed_at"]),
    ("insight_snapshots", "ix_insight_snapshots_business_id", ["business_id"]),
    ("insight_snapshots", "ix_insight_snapshots_observed_at", ["observed_at"]),
    ("trend_reports", "ix_trend_reports_business_id", ["business_id"]),
    ("trend_reports", "ix_trend_reports_status", ["status"]),
    ("content_opportunities", "ix_content_opportunities_business_id", ["business_id"]),
    ("content_opportunities", "ix_content_opportunities_status", ["status"]),
    ("content_opportunities", "ix_content_opportunities_expires_at", ["expires_at"]),
)


def upgrade() -> None:
    bind = op.get_bind()
    import db.models  # noqa: F401
    from db.base import Base
    from db.models import (
        AccountSnapshot,
        ContentOpportunity,
        InsightSnapshot,
        MediaSnapshot,
        TrendEvidence,
        TrendSource,
    )

    Base.metadata.create_all(
        bind=bind,
        tables=[
            TrendSource.__table__,
            TrendEvidence.__table__,
            AccountSnapshot.__table__,
            MediaSnapshot.__table__,
            InsightSnapshot.__table__,
            ContentOpportunity.__table__,
        ],
    )
    _add_missing_columns("trend_observations", _OBSERVATION_COLUMNS)
    _add_missing_columns("trend_reports", _REPORT_COLUMNS)
    _ensure_indexes()
    _ensure_duplicate_index()


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    present = set(inspector.get_table_names())
    for name in (
        "content_opportunities",
        "insight_snapshots",
        "media_snapshots",
        "account_snapshots",
        "trend_evidence",
    ):
        if name in present:
            op.drop_table(name)
    for table, index_name in (
        ("trend_observations", "uq_trend_observations_source_external"),
        ("trend_observations", "ix_trend_observations_source_id"),
        ("trend_observations", "ix_trend_observations_valid_until"),
        ("trend_observations", "ix_trend_observations_trend_type"),
        ("trend_observations", "ix_trend_observations_status"),
        ("trend_observations", "ix_trend_observations_lookup"),
        ("trend_reports", "ix_trend_reports_business_id"),
        ("trend_reports", "ix_trend_reports_status"),
    ):
        _drop_index(table, index_name)
    if "trend_observations" in present:
        _restore_trend_observations()
    if "trend_reports" in present:
        _restore_trend_reports()
    inspector = inspect(bind)
    if "trend_sources" in inspector.get_table_names():
        op.drop_table("trend_sources")


def _add_missing_columns(table: str, columns: dict[str, sa.Column]) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if table not in inspector.get_table_names():
        return
    present = {column["name"] for column in inspector.get_columns(table)}
    for name, column in columns.items():
        if name not in present:
            op.add_column(table, column)


def _restore_trend_observations() -> None:
    op.execute(text("ALTER TABLE trend_observations RENAME TO trend_observations_v005"))
    op.execute(
        text(
            """
            CREATE TABLE trend_observations (
                id VARCHAR(36) NOT NULL,
                user_id VARCHAR(36) NOT NULL,
                industry VARCHAR(64) NOT NULL,
                region VARCHAR(64) NOT NULL,
                festival VARCHAR(120),
                content_type VARCHAR(32),
                title VARCHAR(160) NOT NULL,
                summary TEXT NOT NULL,
                evidence TEXT NOT NULL,
                source VARCHAR(32) NOT NULL,
                observed_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
                CHECK (source IN ('research', 'meta', 'festival', 'business', 'product', 'brand'))
            )
            """
        )
    )
    op.execute(
        text(
            """
            INSERT INTO trend_observations (
                id, user_id, industry, region, festival, content_type, title, summary,
                evidence, source, observed_at, created_at
            )
            SELECT
                id, user_id, industry, region, festival, content_type, title, summary,
                evidence, source, observed_at, created_at
            FROM trend_observations_v005
            """
        )
    )
    op.execute(text("DROP TABLE trend_observations_v005"))
    op.create_index("ix_trend_observations_user_id", "trend_observations", ["user_id"])
    op.create_index("ix_trend_observations_user_observed", "trend_observations", ["user_id", "observed_at"])
    op.create_index("ix_trend_observations_observed_at", "trend_observations", ["observed_at"])
    op.create_index("ix_trend_observations_industry", "trend_observations", ["industry"])
    op.create_index("ix_trend_observations_region", "trend_observations", ["region"])


def _restore_trend_reports() -> None:
    op.execute(text("ALTER TABLE trend_reports RENAME TO trend_reports_v005"))
    op.execute(
        text(
            """
            CREATE TABLE trend_reports (
                id VARCHAR(36) NOT NULL,
                user_id VARCHAR(36) NOT NULL,
                title VARCHAR(160) NOT NULL,
                summary TEXT NOT NULL,
                industry VARCHAR(64),
                region VARCHAR(64),
                observation_count INTEGER NOT NULL,
                observed_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
            )
            """
        )
    )
    op.execute(
        text(
            """
            INSERT INTO trend_reports (
                id, user_id, title, summary, industry, region, observation_count, observed_at, created_at
            )
            SELECT id, user_id, title, summary, industry, region, observation_count, observed_at, created_at
            FROM trend_reports_v005
            """
        )
    )
    op.execute(text("DROP TABLE trend_reports_v005"))
    op.create_index("ix_trend_reports_user_id", "trend_reports", ["user_id"])
    op.create_index("ix_trend_reports_user_observed", "trend_reports", ["user_id", "observed_at"])


def _drop_index(table: str, name: str) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if table not in inspector.get_table_names():
        return
    existing = {index["name"] for index in inspector.get_indexes(table)}
    if name in existing:
        op.drop_index(name, table_name=table)


def _ensure_indexes() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    for table, name, columns in _INDEXES:
        if table not in tables:
            continue
        existing = {index["name"] for index in inspector.get_indexes(table)}
        if name not in existing:
            op.create_index(name, table, columns)


def _ensure_duplicate_index() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "trend_observations" not in inspector.get_table_names():
        return
    existing = {index["name"] for index in inspector.get_indexes("trend_observations")}
    if "uq_trend_observations_source_external" in existing:
        return
    op.create_index(
        "uq_trend_observations_source_external",
        "trend_observations",
        ["user_id", "source_id", "external_id"],
        unique=True,
        sqlite_where=text("external_id IS NOT NULL AND source_id IS NOT NULL"),
        postgresql_where=text("external_id IS NOT NULL AND source_id IS NOT NULL"),
    )
