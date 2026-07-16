"""Move recoverable coordination state from Redis to PostgreSQL.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("runtime_state"):
        return
    op.create_table(
        "runtime_state",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("string_value", sa.Text(), nullable=True),
        sa.Column("counter_value", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_runtime_state_expires_at", "runtime_state", ["expires_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("runtime_state"):
        op.drop_table("runtime_state")
