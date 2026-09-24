"""Brand, product, and tenant asset tables.

Revision ID: 002_brand_assets
Revises: 001_initial_schema
Create Date: 2026-09-24
"""

from alembic import op

from db.base import Base
import db.models  # noqa: F401
from db.models import BrandGuideline, BrandProfile, BusinessAsset, Product, ProductAsset

revision = "002_brand_assets"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None

_TABLES = [
    BusinessAsset.__table__,
    BrandProfile.__table__,
    BrandGuideline.__table__,
    Product.__table__,
    ProductAsset.__table__,
]


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, tables=_TABLES)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, tables=list(reversed(_TABLES)))
