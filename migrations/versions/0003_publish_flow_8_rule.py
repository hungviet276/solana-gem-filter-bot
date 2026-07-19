"""Publish the whale-flow gate rule.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

V1_VERSION = "GEM_FILTER_V1"
V2_VERSION = "GEM_FILTER_V2_FLOW_8"

V1_CONFIG: dict[str, Any] = {
    "strategy": "V1_SCORE",
    "max_snapshot_age_seconds": 20,
    "expiry_seconds": 3600,
    "mc_discrepancy_warn_pct": "30",
    "min_liquidity_usd": "5000",
    "dev_hard_limit_pct": "10",
    "poor_hard_limit_pct": None,
    "soft_chase_ratio": "1.5",
    "hard_chase_ratio": "2.0",
    "watch_threshold": 15,
    "early_threshold": 40,
    "strong_threshold": 55,
}

V2_CONFIG: dict[str, Any] = {
    **V1_CONFIG,
    "strategy": "FLOW_GATE_V2",
    "whale_window_seconds": 900,
    "min_whale_flow_sol": "8",
    "quality_top10_min_pct": "20",
    "quality_top10_max_pct": "25",
    "genesis_max_age_seconds": 600,
    "genesis_max_holders": 200,
    "genesis_first50_min_pct": "50",
    "confirmation_distinct_whales": 2,
}

rule_versions = sa.table(
    "rule_versions",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("version", sa.String(length=64)),
    sa.column("status", sa.String(length=20)),
    sa.column("config", postgresql.JSONB()),
    sa.column("shadow", sa.Boolean()),
    sa.column("published_at", sa.DateTime(timezone=True)),
    sa.column("created_by", sa.String(length=255)),
)


def _publish(version: str, config: dict[str, Any], actor: str) -> None:
    bind = op.get_bind()
    now = datetime.now(tz=UTC)
    bind.execute(
        rule_versions.update()
        .where(rule_versions.c.status == "PUBLISHED")
        .values(status="ARCHIVED")
    )
    existing = bind.execute(
        sa.select(rule_versions.c.id).where(rule_versions.c.version == version)
    ).scalar_one_or_none()
    if existing is None:
        bind.execute(
            rule_versions.insert().values(
                id=uuid.uuid4(),
                version=version,
                status="PUBLISHED",
                config=config,
                shadow=False,
                published_at=now,
                created_by=actor,
            )
        )
    else:
        bind.execute(
            rule_versions.update()
            .where(rule_versions.c.version == version)
            .values(
                status="PUBLISHED",
                config=config,
                shadow=False,
                published_at=now,
            )
        )


def upgrade() -> None:
    _publish(V2_VERSION, V2_CONFIG, "migration-0003")


def downgrade() -> None:
    _publish(V1_VERSION, V1_CONFIG, "migration-0003-downgrade")
