"""Persist image QA on generated_images.

Revision ID: 004_generated_image_qa
Revises: 003_canva_connections
Create Date: 2026-09-24

Fresh databases created from current models already include these columns.
This revision adds them only when an older generated_images table is missing them.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "004_generated_image_qa"
down_revision = "003_canva_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "generated_images" not in inspector.get_table_names():
        return
    present = {column["name"] for column in inspector.get_columns("generated_images")}
    if "qa_status" not in present:
        op.add_column("generated_images", sa.Column("qa_status", sa.String(32), nullable=True))
    if "qa_json" not in present:
        op.add_column("generated_images", sa.Column("qa_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "generated_images" not in inspector.get_table_names():
        return
    present = {column["name"] for column in inspector.get_columns("generated_images")}
    if "qa_json" in present:
        op.drop_column("generated_images", "qa_json")
    if "qa_status" in present:
        op.drop_column("generated_images", "qa_status")
