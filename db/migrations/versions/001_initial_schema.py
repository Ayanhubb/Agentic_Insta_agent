"""Initial platform schema.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-09-21
"""

from alembic import op

from db.base import Base
import db.models  # noqa: F401

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
