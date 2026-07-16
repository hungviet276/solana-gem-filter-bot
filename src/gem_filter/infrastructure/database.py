from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from gem_filter.config import Settings


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TelegramSource(Base, TimestampMixin):
    __tablename__ = "telegram_sources"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    connection_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_message_id: Mapped[int | None] = mapped_column(BigInteger)


class RawTelegramEvent(Base, TimestampMixin):
    __tablename__ = "raw_telegram_events"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "source_message_id", "event_kind", "revision", name="uq_raw_event_business"
        ),
        Index("ix_raw_event_time", "event_ts"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("telegram_sources.id"), nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger)
    event_kind: Mapped[str] = mapped_column(String(30), nullable=False, default="NEW")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    telegram_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class ParsedMessage(Base, TimestampMixin):
    __tablename__ = "parsed_messages"
    __table_args__ = (
        UniqueConstraint("raw_event_id", "parser_version", name="uq_parsed_revision"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("raw_telegram_events.id"), nullable=False
    )
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PARSED")


class RuleVersion(Base, TimestampMixin):
    __tablename__ = "rule_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    shadow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)


class TokenCallRecord(Base, TimestampMixin):
    __tablename__ = "token_calls"
    __table_args__ = (
        UniqueConstraint("source_id", "source_message_id", name="uq_token_source_message"),
        Index("ix_token_contract", "contract"),
        Index("ix_token_status_call_ts", "status", "call_ts"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("telegram_sources.id"), nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    contract: Mapped[str] = mapped_column(String(64), nullable=False)
    ticker: Mapped[str] = mapped_column(String(64), nullable=False)
    token_name: Mapped[str | None] = mapped_column(String(255))
    call_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    call_mc_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    age_seconds: Mapped[int | None] = mapped_column(Integer)
    holders: Mapped[int | None] = mapped_column(Integer)
    top10_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    bundled_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    first50_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    jeeters_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    quickflip_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    snipers_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    insiders_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    dev_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    safe_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    poor_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_split: Mapped[str] = mapped_column(String(30), nullable=False, default="OUT_OF_SAMPLE")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="RECEIVED")
    state_reason: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TokenStateHistory(Base, TimestampMixin):
    __tablename__ = "token_state_history"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("token_calls.id"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False, default="system")


class WhaleEventRecord(Base, TimestampMixin):
    __tablename__ = "whale_events"
    __table_args__ = (
        UniqueConstraint("raw_event_id", name="uq_whale_raw_event"),
        Index("ix_whale_token_time", "token_call_id", "event_ts"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("raw_telegram_events.id"), nullable=False
    )
    token_call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("token_calls.id"))
    whale_label: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_label: Mapped[str] = mapped_column(String(255), nullable=False)
    wallet_cluster: Mapped[str | None] = mapped_column(String(255))
    sol_amount: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    whale_mc_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    before_x2: Mapped[bool | None] = mapped_column(Boolean)
    verification_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="NOT_REQUESTED"
    )
    signature: Mapped[str | None] = mapped_column(String(128))
    orphan_reason: Mapped[str | None] = mapped_column(String(255))


class MultipleEventRecord(Base, TimestampMixin):
    __tablename__ = "multiple_events"
    __table_args__ = (UniqueConstraint("raw_event_id", name="uq_multiple_raw_event"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("raw_telegram_events.id"), nullable=False
    )
    token_call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("token_calls.id"))
    multiple: Mapped[int] = mapped_column(Integer, nullable=False)
    peak_mc_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    elapsed_seconds: Mapped[int | None] = mapped_column(Integer)
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    orphan_reason: Mapped[str | None] = mapped_column(String(255))


class MarketSnapshotRecord(Base, TimestampMixin):
    __tablename__ = "market_snapshots"
    __table_args__ = (Index("ix_market_token_time", "token_call_id", "provider_ts"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("token_calls.id"), nullable=False)
    capture_reason: Mapped[str] = mapped_column(String(30), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    pair_address: Mapped[str | None] = mapped_column(String(128))
    dex: Mapped[str | None] = mapped_column(String(64))
    market_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    fdv_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    liquidity_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    price_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 18))
    volume_24h_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 8))
    buys_5m: Mapped[int | None] = mapped_column(Integer)
    sells_5m: Mapped[int | None] = mapped_column(Integer)
    selection_reason: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class ScoreEvaluationRecord(Base, TimestampMixin):
    __tablename__ = "score_evaluations"
    __table_args__ = (Index("ix_score_token_time", "token_call_id", "evaluated_at"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("token_calls.id"), nullable=False)
    market_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("market_snapshots.id"))
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    hard_reject: Mapped[bool] = mapped_column(Boolean, nullable=False)
    components: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    hard_checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    structures: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    shadow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AlertRecord(Base, TimestampMixin):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint(
            "token_call_id", "level", "destination_chat_id", name="uq_alert_level_destination"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("token_calls.id"), nullable=False)
    score_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("score_evaluations.id"), nullable=False
    )
    destination_chat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    rendered_text: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class OutboxEvent(Base, TimestampMixin):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_outbox_idempotency"),
        Index("ix_outbox_pending", "status", "available_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class WhaleProfile(Base, TimestampMixin):
    __tablename__ = "whale_profiles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    wallets: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    cluster_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    trust_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    observed_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ContractBlacklist(Base, TimestampMixin):
    __tablename__ = "contract_blacklist"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class UserAccount(Base, TimestampMixin):
    __tablename__ = "user_accounts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_salt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    password_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class UserSubscription(Base, TimestampMixin):
    __tablename__ = "users_subscriptions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_accounts.id"))
    destination_chat_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    levels: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    minimum_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quiet_hours: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    correlation_id: Mapped[str | None] = mapped_column(String(64))


class BacktestRun(Base, TimestampMixin):
    __tablename__ = "backtest_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_split: Mapped[str] = mapped_column(String(30), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


class RuntimeState(Base, TimestampMixin):
    """Small recoverable coordination state formerly stored in Redis."""

    __tablename__ = "runtime_state"
    __table_args__ = (Index("ix_runtime_state_expires_at", "expires_at"),)

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    string_value: Mapped[str | None] = mapped_column(Text)
    counter_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def create_engine_and_session(
    settings: Settings,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_recycle=1800)
    return engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
