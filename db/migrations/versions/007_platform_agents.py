"""Platform agents, immutable versions, and executions.

Revision ID: 007_platform_agents
Revises: 006_trend_intelligence
Create Date: 2026-09-28

Fresh databases created from current models already include these tables.
This revision adds them when an older database stopped at 006_trend_intelligence.
"""

from alembic import op
from sqlalchemy import inspect

from db.base import Base
import db.models  # noqa: F401
from db.models import DurableAgent, DurableAgentExecution, DurableAgentVersion

revision = "007_platform_agents"
down_revision = "006_trend_intelligence"
branch_labels = None
depends_on = None

_TABLES = [
    DurableAgent.__table__,
    DurableAgentVersion.__table__,
    DurableAgentExecution.__table__,
]


def upgrade() -> None:
    bind = op.get_bind()
    present = set(inspect(bind).get_table_names())
    missing = [table for table in _TABLES if table.name not in present]
    if missing:
        Base.metadata.create_all(bind=bind, tables=missing)


def downgrade() -> None:
    bind = op.get_bind()
    present = set(inspect(bind).get_table_names())
    for table in reversed(_TABLES):
        if table.name in present:
            Base.metadata.drop_all(bind=bind, tables=[table])
