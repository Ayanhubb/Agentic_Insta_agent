"""Canva connection tables.

Revision ID: 003_canva_connections
Revises: 002_brand_assets
Create Date: 2026-09-24
"""

from alembic import op

from db.base import Base
import db.models  # noqa: F401
from db.models import CanvaConnection, CanvaOAuthState

revision = "003_canva_connections"
down_revision = "002_brand_assets"
branch_labels = None
depends_on = None

_TABLES = [CanvaConnection.__table__, CanvaOAuthState.__table__]


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, tables=_TABLES)


def downgrade() -> None:
    bind = op.get_bind()
    CanvaOAuthState.__table__.drop(bind=bind, checkfirst=True)
    CanvaConnection.__table__.drop(bind=bind, checkfirst=True)
