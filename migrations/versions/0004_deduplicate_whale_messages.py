"""Deduplicate whale events by logical Telegram message.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UNIQUE_NAME = "uq_whale_source_message"
SOURCE_FK_NAME = "fk_whale_events_source_id_telegram_sources"


def _column_names(bind: sa.Connection) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns("whale_events")}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind)
    if "source_id" not in columns:
        op.add_column(
            "whale_events",
            sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
    if "source_message_id" not in columns:
        op.add_column(
            "whale_events",
            sa.Column("source_message_id", sa.BigInteger(), nullable=True),
        )

    bind.execute(
        sa.text(
            """
            UPDATE whale_events AS whale
            SET source_id = raw.source_id,
                source_message_id = raw.source_message_id
            FROM raw_telegram_events AS raw
            WHERE raw.id = whale.raw_event_id
              AND (
                  whale.source_id IS DISTINCT FROM raw.source_id
                  OR whale.source_message_id IS DISTINCT FROM raw.source_message_id
              )
            """
        )
    )
    bind.execute(
        sa.text(
            """
            DELETE FROM whale_events AS whale
            USING (
                SELECT id
                FROM (
                    SELECT
                        whale.id,
                        ROW_NUMBER() OVER (
                            PARTITION BY whale.source_id, whale.source_message_id
                            ORDER BY
                                raw.revision DESC,
                                raw.ingested_at DESC,
                                whale.created_at DESC,
                                whale.id DESC
                        ) AS duplicate_rank
                    FROM whale_events AS whale
                    JOIN raw_telegram_events AS raw ON raw.id = whale.raw_event_id
                ) AS ranked
                WHERE duplicate_rank > 1
            ) AS duplicate
            WHERE whale.id = duplicate.id
            """
        )
    )

    null_count = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM whale_events
            WHERE source_id IS NULL OR source_message_id IS NULL
            """
        )
    ).scalar_one()
    if null_count:
        raise RuntimeError("cannot backfill Telegram identity for every whale event")

    op.alter_column(
        "whale_events",
        "source_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "whale_events",
        "source_message_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )

    inspector = sa.inspect(bind)
    source_foreign_key_exists = any(
        constraint["constrained_columns"] == ["source_id"]
        for constraint in inspector.get_foreign_keys("whale_events")
    )
    if not source_foreign_key_exists:
        op.create_foreign_key(
            SOURCE_FK_NAME,
            "whale_events",
            "telegram_sources",
            ["source_id"],
            ["id"],
        )
    unique_constraints = {
        constraint["name"] for constraint in sa.inspect(bind).get_unique_constraints("whale_events")
    }
    if UNIQUE_NAME not in unique_constraints:
        op.create_unique_constraint(
            UNIQUE_NAME,
            "whale_events",
            ["source_id", "source_message_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    unique_constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("whale_events")
    }
    if UNIQUE_NAME in unique_constraints:
        op.drop_constraint(UNIQUE_NAME, "whale_events", type_="unique")

    source_foreign_key = next(
        (
            constraint
            for constraint in sa.inspect(bind).get_foreign_keys("whale_events")
            if constraint["constrained_columns"] == ["source_id"]
        ),
        None,
    )
    if source_foreign_key is not None and source_foreign_key["name"] is not None:
        op.drop_constraint(source_foreign_key["name"], "whale_events", type_="foreignkey")

    columns = _column_names(bind)
    if "source_message_id" in columns:
        op.drop_column("whale_events", "source_message_id")
    if "source_id" in columns:
        op.drop_column("whale_events", "source_id")
