"""Trend research tables read by the Trend MCP server.

Revision ID: 005_trend_research
Revises: 004_generated_image_qa
Create Date: 2026-09-24
"""

from alembic import op

from db.base import Base
import db.models  # noqa: F401
from db.models import TrendObservation, TrendReport, TrendSource

revision = "005_trend_research"
down_revision = "004_generated_image_qa"
branch_labels = None
depends_on = None

_TABLES = [TrendSource.__table__, TrendObservation.__table__, TrendReport.__table__]


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, tables=_TABLES)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, tables=list(reversed(_TABLES)))
