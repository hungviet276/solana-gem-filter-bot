"""Initial SRS v1 schema.

Revision ID: 0001
Revises: None
"""

from alembic import op

from gem_filter.infrastructure.database import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
