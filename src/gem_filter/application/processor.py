from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gem_filter.application.live_evaluation import evaluate_live
from gem_filter.config import Settings
from gem_filter.domain.alerts import render_alert, render_early_candidate_x2
from gem_filter.domain.early_candidate import (
    EARLY_CANDIDATE_X2_LEVEL,
    evaluate_early_candidate_x2,
)
from gem_filter.domain.models import (
    DatasetSplit,
    Decision,
    MarketSnapshot,
    MultipleEvent,
    TokenCall,
    TokenStatus,
    VerificationStatus,
    WhaleEvent,
)
from gem_filter.domain.parser import PARSER_VERSION, parse_message
from gem_filter.domain.rules import RuleConfig, builtin_rule, evaluate
from gem_filter.domain.state import status_for_decision, transition
from gem_filter.infrastructure.database import (
    AlertRecord,
    AuditLog,
    ContractBlacklist,
    MarketSnapshotRecord,
    MultipleEventRecord,
    OutboxEvent,
    ParsedMessage,
    RawTelegramEvent,
    RuleVersion,
    ScoreEvaluationRecord,
    TelegramSource,
    TokenCallRecord,
    TokenStateHistory,
    UserSubscription,
    WhaleEventRecord,
)
from gem_filter.observability import (
    DUPLICATE_PREVENTED,
    ORPHAN_EVENTS,
    SCORE_EVALUATIONS,
    TELEGRAM_EVENTS,
)

logger = logging.getLogger(__name__)


class MarketProvider(Protocol):
    async def fetch(
        self, contract: str, *, retries: int = 2
    ) -> tuple[MarketSnapshot, dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class TelegramEnvelope:
    source_channel_id: int
    source_message_id: int
    event_ts: datetime
    raw_text: str
    reply_to_message_id: int | None = None
    edited: bool = False
    revision: int = 0
    raw_payload: dict[str, Any] | None = None


def _rule_from_payload(payload: dict[str, Any] | None, version: str) -> RuleConfig:
    if not payload:
        return builtin_rule(version)
    decimal_fields = {
        "mc_discrepancy_warn_pct",
        "min_liquidity_usd",
        "dev_hard_limit_pct",
        "poor_hard_limit_pct",
        "soft_chase_ratio",
        "hard_chase_ratio",
        "min_whale_flow_sol",
        "quality_top10_min_pct",
        "quality_top10_max_pct",
        "genesis_first50_min_pct",
    }
    values: dict[str, Any] = {"version": version}
    allowed = RuleConfig.__dataclass_fields__.keys()
    for key, value in payload.items():
        if key not in allowed or key == "version":
            continue
        values[key] = Decimal(str(value)) if key in decimal_fields and value is not None else value
    return RuleConfig(**values)


class ProcessingService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        market_provider: MarketProvider,
        settings: Settings,
    ) -> None:
        self.sessions = session_factory
        self.market_provider = market_provider
        self.settings = settings

    async def ingest(self, envelope: TelegramEnvelope) -> dict[str, Any]:
        """Durably persist a raw event and enqueue processing in one short transaction."""
        if envelope.event_ts.tzinfo is None:
            raise ValueError("event_ts must be timezone-aware")
        async with self.sessions() as session, session.begin():
            source = await session.scalar(
                select(TelegramSource).where(
                    TelegramSource.channel_id == envelope.source_channel_id
                )
            )
            if source is None or not source.enabled:
                raise LookupError("Telegram source is not configured or disabled")
            event_kind = "EDITED" if envelope.edited else "NEW"
            existing = await session.scalar(
                select(RawTelegramEvent).where(
                    RawTelegramEvent.source_id == source.id,
                    RawTelegramEvent.source_message_id == envelope.source_message_id,
                    RawTelegramEvent.event_kind == event_kind,
                    RawTelegramEvent.revision == envelope.revision,
                )
            )
            if existing is not None:
                DUPLICATE_PREVENTED.labels("raw_event").inc()
                return {"status": "duplicate", "event_id": str(existing.id)}
            raw = RawTelegramEvent(
                source_id=source.id,
                source_message_id=envelope.source_message_id,
                reply_to_message_id=envelope.reply_to_message_id,
                event_kind=event_kind,
                revision=envelope.revision,
                event_ts=envelope.event_ts.astimezone(UTC),
                raw_text=envelope.raw_text,
                raw_text_hash=hashlib.sha256(envelope.raw_text.encode("utf-8")).hexdigest(),
                telegram_payload=envelope.raw_payload or {},
            )
            session.add(raw)
            await session.flush()
            session.add(
                OutboxEvent(
                    topic="PROCESS_RAW_EVENT",
                    idempotency_key=f"raw:{raw.id}:process:{PARSER_VERSION}",
                    payload={"raw_event_id": str(raw.id)},
                )
            )
            source.last_message_id = max(source.last_message_id or 0, envelope.source_message_id)
            return {"status": "accepted", "event_id": str(raw.id)}

    async def process_raw(self, raw_event_id: uuid.UUID) -> dict[str, Any]:
        """Parse and apply one previously committed raw event."""
        async with self.sessions() as session, session.begin():
            raw = await session.get(RawTelegramEvent, raw_event_id)
            if raw is None:
                raise LookupError("raw event not found")
            existing = await session.scalar(
                select(ParsedMessage).where(
                    ParsedMessage.raw_event_id == raw.id,
                    ParsedMessage.parser_version == PARSER_VERSION,
                )
            )
            if existing is not None:
                DUPLICATE_PREVENTED.labels("parsed_event").inc()
                return {"status": "duplicate", "event_id": str(raw.id)}
            source = await session.get(TelegramSource, raw.source_id)
            if source is None:
                raise LookupError("Telegram source no longer exists")
            parsed = parse_message(raw.raw_text)
            session.add(
                ParsedMessage(
                    raw_event_id=raw.id,
                    parser_version=parsed.parser_version,
                    event_type=parsed.event_type.value,
                    payload=parsed.payload,
                    warnings=list(parsed.warnings),
                    status="PARSED" if parsed.event_type.value != "UNKNOWN" else "REVIEW",
                )
            )
            TELEGRAM_EVENTS.labels(parsed.event_type.value, "parsed").inc()
            handler = {
                "CALL": self._process_call,
                "WHALE_BUY": self._process_whale,
                "MULTIPLE_RESULT": self._process_multiple,
            }.get(parsed.event_type.value)
            result: dict[str, Any] = {
                "status": "processed",
                "event_id": str(raw.id),
                "event_type": parsed.event_type.value,
                "warnings": list(parsed.warnings),
            }
            if handler:
                result.update(await handler(session, source, raw, parsed.payload))
            return result

    async def scheduled_evaluate(self, token_call_id: uuid.UUID, capture_reason: str) -> uuid.UUID:
        async with self.sessions() as session, session.begin():
            token = await session.get(TokenCallRecord, token_call_id, with_for_update=True)
            if token is None:
                raise LookupError("token call not found")
            if token.status in {"CLOSED", "EXPIRED", "REJECTED"}:
                raise ValueError("token is not active")
            rule = await self._rule_for_token(session, token)
            return await self._snapshot_evaluate_and_queue(session, token, rule, capture_reason)

    async def revalidate_alert(self, alert_id: uuid.UUID) -> dict[str, Any] | None:
        """Fresh market gate immediately before sending an external alert."""
        async with self.sessions() as session, session.begin():
            alert = await session.get(AlertRecord, alert_id, with_for_update=True)
            if alert is None:
                raise LookupError("alert not found")
            if alert.status == "SENT":
                return None
            token = await session.get(TokenCallRecord, alert.token_call_id)
            if token is None:
                raise LookupError("token call not found")
            rule = await self._rule_for_token(session, token)
            try:
                market, raw_pair = await self.market_provider.fetch(token.contract)
            except Exception:
                alert.status = "BLOCKED_STALE"
                alert.error = "Fresh market lookup failed at send gate"
                return None
            market_record = MarketSnapshotRecord(
                token_call_id=token.id,
                capture_reason="ALERT_GATE",
                captured_at=market.captured_at,
                provider_ts=market.provider_ts,
                provider=market.provider,
                pair_address=market.pair_address,
                dex=market.dex,
                market_cap_usd=market.market_cap_usd,
                fdv_usd=market.fdv_usd,
                liquidity_usd=market.liquidity_usd,
                price_usd=market.price_usd,
                volume_24h_usd=market.volume_24h_usd,
                buys_5m=market.buys_5m,
                sells_5m=market.sells_5m,
                selection_reason=market.selection_reason,
                raw_payload=raw_pair,
            )
            session.add(market_record)
            await session.flush()
            whales = (
                await session.scalars(
                    select(WhaleEventRecord)
                    .where(WhaleEventRecord.token_call_id == token.id)
                    .order_by(WhaleEventRecord.event_ts)
                )
            ).all()
            outcomes = (
                await session.scalars(
                    select(MultipleEventRecord)
                    .where(MultipleEventRecord.token_call_id == token.id)
                    .order_by(MultipleEventRecord.event_ts)
                )
            ).all()
            domain_token = _domain_token(token)
            domain_whales = tuple(_domain_whale(item) for item in whales)
            evaluation = evaluate_live(
                domain_token,
                market,
                domain_whales,
                tuple(_domain_multiple(item) for item in outcomes),
                rule,
            )
            score = ScoreEvaluationRecord(
                token_call_id=token.id,
                market_snapshot_id=market_record.id,
                rule_version=evaluation.rule_version,
                evaluated_at=evaluation.evaluated_at,
                score=evaluation.score,
                decision=evaluation.decision.value,
                hard_reject=evaluation.hard_reject,
                components=[asdict(component) for component in evaluation.components],
                hard_checks=[asdict(check) for check in evaluation.hard_checks],
                structures=list(evaluation.structures),
                input_hash=evaluation.input_hash,
                shadow=False,
            )
            session.add(score)
            await session.flush()
            allowed_levels = {Decision.EARLY.value, Decision.STRONG.value}
            if evaluation.hard_reject or evaluation.decision.value not in allowed_levels:
                alert.status = "BLOCKED_REVALIDATION"
                alert.error = "Decision no longer qualifies at fresh send gate"
                return None
            if alert.level == "STRONG" and evaluation.decision is not Decision.STRONG:
                alert.status = "BLOCKED_DOWNGRADE"
                alert.error = "Strong signal downgraded at send gate"
                return None
            alert.score_evaluation_id = score.id
            alert.rendered_text = render_alert(domain_token, market, domain_whales, evaluation)
            return {
                "alert_id": str(alert.id),
                "chat_id": alert.destination_chat_id,
                "text": alert.rendered_text,
                "level": alert.level,
                "contract": token.contract,
                "chart_url": f"https://dexscreener.com/solana/{market.pair_address}"
                if market.pair_address
                else None,
                "details_url": (
                    f"{self.settings.public_base_url.rstrip('/')}/?token={token.id}"
                    if self.settings.public_base_url.strip()
                    else None
                ),
            }

    async def prepare_early_candidate_x2_alert(
        self, alert_id: uuid.UUID
    ) -> dict[str, Any] | None:
        """Recheck only initial-call fields; never load market, whale or outcome data."""

        async with self.sessions() as session, session.begin():
            alert = await session.get(AlertRecord, alert_id, with_for_update=True)
            if alert is None:
                raise LookupError("alert not found")
            if alert.level != EARLY_CANDIDATE_X2_LEVEL:
                raise ValueError("alert does not belong to the early candidate X2 flow")
            if alert.status == "SENT":
                return None
            if not self.settings.early_candidate_x2_enabled:
                alert.status = "SUPPRESSED_DISABLED"
                alert.error = "EARLY_CANDIDATE_X2_ENABLED is false"
                return None
            token = await session.get(TokenCallRecord, alert.token_call_id)
            if token is None:
                raise LookupError("token call not found")
            blacklist = await session.scalar(
                select(ContractBlacklist).where(
                    ContractBlacklist.contract == token.contract,
                    ContractBlacklist.active.is_(True),
                )
            )
            if blacklist is not None:
                alert.status = "BLOCKED_REVALIDATION"
                alert.error = "Contract was blacklisted before early alert delivery"
                return None
            domain_token = _domain_token(token)
            evaluation = evaluate_early_candidate_x2(domain_token)
            if not evaluation.matched:
                alert.status = "BLOCKED_REVALIDATION"
                alert.error = "Initial-call fields no longer match EARLY_CANDIDATE_X2_V1"
                return None
            alert.rendered_text = render_early_candidate_x2(domain_token, evaluation)
            return {
                "alert_id": str(alert.id),
                "chat_id": alert.destination_chat_id,
                "text": alert.rendered_text,
                "level": alert.level,
                "contract": token.contract,
                "chart_url": f"https://dexscreener.com/solana/{token.contract}",
                "details_url": None,
            }

    async def process(self, envelope: TelegramEnvelope) -> dict[str, Any]:
        if envelope.event_ts.tzinfo is None:
            raise ValueError("event_ts must be timezone-aware")
        async with self.sessions() as session, session.begin():
            source = await session.scalar(
                select(TelegramSource).where(
                    TelegramSource.channel_id == envelope.source_channel_id
                )
            )
            if source is None or not source.enabled:
                raise LookupError("Telegram source is not configured or disabled")

            event_kind = "EDITED" if envelope.edited else "NEW"
            existing = await session.scalar(
                select(RawTelegramEvent).where(
                    RawTelegramEvent.source_id == source.id,
                    RawTelegramEvent.source_message_id == envelope.source_message_id,
                    RawTelegramEvent.event_kind == event_kind,
                    RawTelegramEvent.revision == envelope.revision,
                )
            )
            if existing is not None:
                DUPLICATE_PREVENTED.labels("raw_event").inc()
                return {"status": "duplicate", "event_id": str(existing.id)}

            raw_hash = hashlib.sha256(envelope.raw_text.encode("utf-8")).hexdigest()
            raw = RawTelegramEvent(
                source_id=source.id,
                source_message_id=envelope.source_message_id,
                reply_to_message_id=envelope.reply_to_message_id,
                event_kind=event_kind,
                revision=envelope.revision,
                event_ts=envelope.event_ts.astimezone(UTC),
                raw_text=envelope.raw_text,
                raw_text_hash=raw_hash,
                telegram_payload=envelope.raw_payload or {},
            )
            session.add(raw)
            await session.flush()

            parsed = parse_message(envelope.raw_text)
            session.add(
                ParsedMessage(
                    raw_event_id=raw.id,
                    parser_version=parsed.parser_version,
                    event_type=parsed.event_type.value,
                    payload=parsed.payload,
                    warnings=list(parsed.warnings),
                    status="PARSED" if parsed.event_type.value != "UNKNOWN" else "REVIEW",
                )
            )
            source.last_message_id = max(source.last_message_id or 0, envelope.source_message_id)
            TELEGRAM_EVENTS.labels(parsed.event_type.value, "parsed").inc()

            handler = {
                "CALL": self._process_call,
                "WHALE_BUY": self._process_whale,
                "MULTIPLE_RESULT": self._process_multiple,
            }.get(parsed.event_type.value)
            result: dict[str, Any] = {
                "status": "stored",
                "event_id": str(raw.id),
                "event_type": parsed.event_type.value,
                "warnings": list(parsed.warnings),
            }
            if handler:
                result.update(await handler(session, source, raw, parsed.payload))
            return result

    async def _active_rule(self, session: AsyncSession) -> RuleConfig:
        record = await session.scalar(
            select(RuleVersion)
            .where(RuleVersion.status == "PUBLISHED", RuleVersion.shadow.is_(False))
            .order_by(RuleVersion.published_at.desc())
            .limit(1)
        )
        if record is None:
            return builtin_rule(self.settings.active_rule_version)
        return _rule_from_payload(record.config, record.version)

    async def _process_call(
        self,
        session: AsyncSession,
        source: TelegramSource,
        raw: RawTelegramEvent,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        required = {"contract", "ticker"}
        if not required.issubset(payload):
            return {"business_status": "manual_review", "reason": "missing contract or ticker"}

        existing = await session.scalar(
            select(TokenCallRecord).where(
                TokenCallRecord.source_id == source.id,
                TokenCallRecord.source_message_id == raw.source_message_id,
            )
        )
        if existing is not None:
            if raw.event_kind == "EDITED":
                await self._audit(
                    session,
                    "system",
                    "MESSAGE_EDIT_REPARSE_REQUIRED",
                    "token_call",
                    str(existing.id),
                    "Edited call preserved as immutable raw revision",
                )
            return {"token_call_id": str(existing.id), "business_status": "already_exists"}

        active_same_contract = await session.scalar(
            select(TokenCallRecord).where(
                TokenCallRecord.contract == payload["contract"],
                TokenCallRecord.status.not_in(["CLOSED", "EXPIRED", "REJECTED"]),
            )
        )
        if active_same_contract is not None:
            return {
                "token_call_id": str(active_same_contract.id),
                "business_status": "active_contract_exists",
            }

        rule = await self._active_rule(session)
        token = TokenCallRecord(
            source_id=source.id,
            source_message_id=raw.source_message_id,
            contract=payload["contract"],
            ticker=payload["ticker"],
            token_name=payload.get("token_name"),
            call_ts=raw.event_ts,
            call_mc_usd=_optional_decimal(payload.get("call_mc_usd")),
            age_seconds=payload.get("age_seconds"),
            holders=payload.get("holders"),
            top10_pct=_optional_decimal(payload.get("top10_pct")),
            bundled_pct=_optional_decimal(payload.get("bundled_pct")),
            first50_pct=_optional_decimal(payload.get("first50_pct")),
            jeeters_pct=_optional_decimal(payload.get("jeeters_pct")),
            quickflip_pct=_optional_decimal(payload.get("quickflip_pct")),
            snipers_pct=_optional_decimal(payload.get("snipers_pct")),
            insiders_pct=_optional_decimal(payload.get("insiders_pct")),
            dev_pct=_optional_decimal(payload.get("dev_pct")),
            safe_pct=_optional_decimal(payload.get("safe_pct")),
            poor_pct=_optional_decimal(payload.get("poor_pct")),
            rule_version=rule.version,
            dataset_split="OUT_OF_SAMPLE",
            status="VERIFYING",
            state_reason="CALL_PARSED",
            expires_at=raw.event_ts + timedelta(seconds=rule.expiry_seconds),
        )
        session.add(token)
        await session.flush()
        session.add(
            TokenStateHistory(
                token_call_id=token.id,
                from_status="RECEIVED",
                to_status="VERIFYING",
                reason="CALL_PARSED",
            )
        )
        early_candidate_x2_queued = await self._queue_early_candidate_x2(session, token)
        evaluation_id = await self._snapshot_evaluate_and_queue(session, token, rule, "CALL")
        return {
            "token_call_id": str(token.id),
            "score_evaluation_id": str(evaluation_id),
            "early_candidate_x2_queued": early_candidate_x2_queued,
            "business_status": token.status,
        }

    async def _process_whale(
        self,
        session: AsyncSession,
        source: TelegramSource,
        raw: RawTelegramEvent,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        existing = await session.scalar(
            select(WhaleEventRecord)
            .where(
                WhaleEventRecord.source_id == source.id,
                WhaleEventRecord.source_message_id == raw.source_message_id,
            )
            .with_for_update()
        )
        if existing is not None:
            existing_raw = await session.get(RawTelegramEvent, existing.raw_event_id)
            if existing_raw is not None and existing_raw.revision > raw.revision:
                DUPLICATE_PREVENTED.labels("whale_stale_revision").inc()
                return {
                    "whale_event_id": str(existing.id),
                    "business_status": "stale_revision",
                }
        token = None
        if raw.reply_to_message_id is not None:
            token = await session.scalar(
                select(TokenCallRecord).where(
                    TokenCallRecord.source_id == source.id,
                    TokenCallRecord.source_message_id == raw.reply_to_message_id,
                )
            )
        if token is None and existing is not None and existing.token_call_id is not None:
            token = await session.get(TokenCallRecord, existing.token_call_id)
        if existing is None:
            event = WhaleEventRecord(
                raw_event_id=raw.id,
                source_id=source.id,
                source_message_id=raw.source_message_id,
                token_call_id=token.id if token else None,
                whale_label=payload.get("whale_label", "unknown"),
                normalized_label=payload.get("normalized_label", "unknown"),
                sol_amount=_optional_decimal(payload.get("sol_amount")),
                whale_mc_usd=_optional_decimal(payload.get("whale_mc_usd")),
                event_ts=raw.event_ts,
                verification_status="NOT_REQUESTED",
                orphan_reason=None if token else "CALL_NOT_FOUND_BY_REPLY_TO",
            )
            session.add(event)
        else:
            event = existing
            event.raw_event_id = raw.id
            event.token_call_id = token.id if token else None
            event.whale_label = payload.get("whale_label", "unknown")
            event.normalized_label = payload.get("normalized_label", "unknown")
            event.sol_amount = _optional_decimal(payload.get("sol_amount"))
            event.whale_mc_usd = _optional_decimal(payload.get("whale_mc_usd"))
            event.event_ts = raw.event_ts
            event.orphan_reason = None if token else "CALL_NOT_FOUND_BY_REPLY_TO"
        if token:
            x2_ts = await session.scalar(
                select(MultipleEventRecord.event_ts)
                .where(
                    MultipleEventRecord.token_call_id == token.id, MultipleEventRecord.multiple >= 2
                )
                .order_by(MultipleEventRecord.event_ts)
                .limit(1)
            )
            event.before_x2 = x2_ts is None or raw.event_ts < x2_ts
        await session.flush()
        if token is None:
            ORPHAN_EVENTS.inc()
            return {"whale_event_id": str(event.id), "business_status": "orphan"}
        rule = await self._rule_for_token(session, token)
        evaluation_id = await self._snapshot_evaluate_and_queue(session, token, rule, "WHALE")
        return {
            "token_call_id": str(token.id),
            "whale_event_id": str(event.id),
            "score_evaluation_id": str(evaluation_id),
            "business_status": token.status,
        }

    async def _process_multiple(
        self,
        session: AsyncSession,
        source: TelegramSource,
        raw: RawTelegramEvent,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        token = None
        if raw.reply_to_message_id is not None:
            token = await session.scalar(
                select(TokenCallRecord).where(
                    TokenCallRecord.source_id == source.id,
                    TokenCallRecord.source_message_id == raw.reply_to_message_id,
                )
            )
        event = MultipleEventRecord(
            raw_event_id=raw.id,
            token_call_id=token.id if token else None,
            multiple=int(payload.get("multiple", 0)),
            peak_mc_usd=_optional_decimal(payload.get("peak_mc_usd")),
            elapsed_seconds=payload.get("elapsed_seconds"),
            event_ts=raw.event_ts,
            orphan_reason=None if token else "CALL_NOT_FOUND_BY_REPLY_TO",
        )
        session.add(event)
        await session.flush()
        if token is None:
            ORPHAN_EVENTS.inc()
            return {"multiple_event_id": str(event.id), "business_status": "orphan"}
        if event.multiple >= 2 and token.status not in {"REJECTED", "EXPIRED", "CLOSED"}:
            await self._set_state(
                session, token, TokenStatus.CONFIRMED, f"MULTIPLE_X{event.multiple}"
            )
        return {
            "token_call_id": str(token.id),
            "multiple_event_id": str(event.id),
            "business_status": token.status,
        }

    async def _rule_for_token(self, session: AsyncSession, token: TokenCallRecord) -> RuleConfig:
        record = await session.scalar(
            select(RuleVersion).where(RuleVersion.version == token.rule_version)
        )
        return _rule_from_payload(record.config if record else None, token.rule_version)

    async def _snapshot_evaluate_and_queue(
        self,
        session: AsyncSession,
        token: TokenCallRecord,
        rule: RuleConfig,
        capture_reason: str,
    ) -> uuid.UUID:
        blacklist = await session.scalar(
            select(ContractBlacklist).where(
                ContractBlacklist.contract == token.contract,
                ContractBlacklist.active.is_(True),
            )
        )
        if blacklist is not None:
            evaluated_at = datetime.now(tz=UTC)
            score_record = ScoreEvaluationRecord(
                token_call_id=token.id,
                market_snapshot_id=None,
                rule_version=rule.version,
                evaluated_at=evaluated_at,
                score=0,
                decision=Decision.REJECTED.value,
                hard_reject=True,
                components=[],
                hard_checks=[
                    {
                        "code": "BLACKLISTED_CONTRACT",
                        "passed": False,
                        "evidence": {"reason": blacklist.reason},
                    }
                ],
                structures=[],
                input_hash=hashlib.sha256(
                    f"{token.id}:{rule.version}:BLACKLISTED_CONTRACT".encode()
                ).hexdigest(),
                shadow=False,
            )
            session.add(score_record)
            await session.flush()
            await self._set_state(session, token, TokenStatus.REJECTED, "BLACKLISTED_CONTRACT")
            return score_record.id
        market: MarketSnapshot | None = None
        market_record: MarketSnapshotRecord | None = None
        try:
            market, raw_pair = await self.market_provider.fetch(token.contract)
            market_record = MarketSnapshotRecord(
                token_call_id=token.id,
                capture_reason=capture_reason,
                captured_at=market.captured_at,
                provider_ts=market.provider_ts,
                provider=market.provider,
                pair_address=market.pair_address,
                dex=market.dex,
                market_cap_usd=market.market_cap_usd,
                fdv_usd=market.fdv_usd,
                liquidity_usd=market.liquidity_usd,
                price_usd=market.price_usd,
                volume_24h_usd=market.volume_24h_usd,
                buys_5m=market.buys_5m,
                sells_5m=market.sells_5m,
                selection_reason=market.selection_reason,
                raw_payload=raw_pair,
            )
            session.add(market_record)
            await session.flush()
        except Exception:
            logger.exception(
                "market snapshot failed",
                extra={"token_call_id": str(token.id), "rule_version": rule.version},
            )

        whale_records = (
            await session.scalars(
                select(WhaleEventRecord)
                .where(WhaleEventRecord.token_call_id == token.id)
                .order_by(WhaleEventRecord.event_ts)
            )
        ).all()
        multiple_records = (
            await session.scalars(
                select(MultipleEventRecord)
                .where(MultipleEventRecord.token_call_id == token.id)
                .order_by(MultipleEventRecord.event_ts)
            )
        ).all()
        domain_token = _domain_token(token)
        domain_whales = tuple(_domain_whale(record) for record in whale_records)
        domain_outcomes = tuple(_domain_multiple(record) for record in multiple_records)
        evaluation = evaluate_live(domain_token, market, domain_whales, domain_outcomes, rule)
        score_record = ScoreEvaluationRecord(
            token_call_id=token.id,
            market_snapshot_id=market_record.id if market_record else None,
            rule_version=evaluation.rule_version,
            evaluated_at=evaluation.evaluated_at,
            score=evaluation.score,
            decision=evaluation.decision.value,
            hard_reject=evaluation.hard_reject,
            components=[asdict(component) for component in evaluation.components],
            hard_checks=[asdict(check) for check in evaluation.hard_checks],
            structures=list(evaluation.structures),
            input_hash=evaluation.input_hash,
            shadow=False,
        )
        session.add(score_record)
        await session.flush()
        SCORE_EVALUATIONS.labels(evaluation.decision.value, evaluation.rule_version).inc()

        target = status_for_decision(evaluation.decision)
        if target is not None:
            await self._set_state(session, token, target, "|".join(evaluation.reason_codes)[:255])

        should_alert = evaluation.decision in {Decision.EARLY, Decision.STRONG} or (
            evaluation.decision is Decision.WATCH and self.settings.watch_alerts_enabled
        )
        if should_alert and market is not None and not self.settings.alerts_kill_switch:
            text = render_alert(domain_token, market, domain_whales, evaluation)
            destinations = await self._alert_destinations(
                session, token, evaluation.decision.value, evaluation.score
            )
            for chat_id in destinations:
                await self._queue_alert(
                    session, token, score_record, chat_id, evaluation.decision.value, text
                )
        await self._evaluate_shadow_rules(
            session,
            token,
            market_record,
            market,
            domain_whales,
            domain_outcomes,
            evaluation.evaluated_at,
        )
        return score_record.id

    async def _alert_destinations(
        self,
        session: AsyncSession,
        token: TokenCallRecord,
        level: str,
        score: int,
    ) -> set[str]:
        destinations = set(self.settings.alert_chat_ids)
        subscriptions = (
            await session.scalars(
                select(UserSubscription).where(UserSubscription.enabled.is_(True))
            )
        ).all()
        now_hhmm = datetime.now(tz=UTC).strftime("%H:%M")
        for subscription in subscriptions:
            if level not in subscription.levels or score < subscription.minimum_score:
                continue
            if subscription.source_ids and str(token.source_id) not in subscription.source_ids:
                continue
            quiet = subscription.quiet_hours or {}
            start, end = quiet.get("start_utc"), quiet.get("end_utc")
            if start and end and _time_in_window(now_hhmm, start, end):
                continue
            destinations.add(subscription.destination_chat_id)
        return destinations

    async def _evaluate_shadow_rules(
        self,
        session: AsyncSession,
        token: TokenCallRecord,
        market_record: MarketSnapshotRecord | None,
        market: MarketSnapshot | None,
        whales: tuple[WhaleEvent, ...],
        outcomes: tuple[MultipleEvent, ...],
        evaluation_time: datetime,
    ) -> None:
        records = (
            await session.scalars(select(RuleVersion).where(RuleVersion.shadow.is_(True)))
        ).all()
        domain_token = _domain_token(token)
        for record in records:
            result = evaluate(
                domain_token,
                market,
                whales,
                outcomes,
                _rule_from_payload(record.config, record.version),
                evaluation_time=evaluation_time,
            )
            session.add(
                ScoreEvaluationRecord(
                    token_call_id=token.id,
                    market_snapshot_id=market_record.id if market_record else None,
                    rule_version=result.rule_version,
                    evaluated_at=result.evaluated_at,
                    score=result.score,
                    decision=result.decision.value,
                    hard_reject=result.hard_reject,
                    components=[asdict(component) for component in result.components],
                    hard_checks=[asdict(check) for check in result.hard_checks],
                    structures=list(result.structures),
                    input_hash=result.input_hash,
                    shadow=True,
                )
            )

    async def _set_state(
        self,
        session: AsyncSession,
        token: TokenCallRecord,
        target: TokenStatus,
        reason: str,
    ) -> None:
        current = TokenStatus(token.status)
        if current in {
            TokenStatus.CONFIRMED,
            TokenStatus.EXPIRED,
            TokenStatus.REJECTED,
            TokenStatus.CLOSED,
        }:
            return
        try:
            new_status = transition(current, target)
        except ValueError:
            logger.warning(
                "ignored invalid state transition %s -> %s",
                current,
                target,
                extra={"token_call_id": str(token.id)},
            )
            return
        if new_status == current:
            return
        token.status = new_status.value
        token.state_reason = reason
        session.add(
            TokenStateHistory(
                token_call_id=token.id,
                from_status=current.value,
                to_status=new_status.value,
                reason=reason,
            )
        )

    async def _queue_alert(
        self,
        session: AsyncSession,
        token: TokenCallRecord,
        score: ScoreEvaluationRecord,
        chat_id: str,
        level: str,
        text: str,
        *,
        topic: str = "TELEGRAM_ALERT",
    ) -> None:
        existing = await session.scalar(
            select(AlertRecord).where(
                AlertRecord.token_call_id == token.id,
                AlertRecord.level == level,
                AlertRecord.destination_chat_id == chat_id,
            )
        )
        if existing is not None:
            DUPLICATE_PREVENTED.labels("alert").inc()
            return
        alert = AlertRecord(
            token_call_id=token.id,
            score_evaluation_id=score.id,
            destination_chat_id=chat_id,
            level=level,
            status="PENDING",
            rendered_text=text,
        )
        session.add(alert)
        await session.flush()
        session.add(
            OutboxEvent(
                topic=topic,
                idempotency_key=f"alert:{token.id}:{level}:{chat_id}",
                payload={"alert_id": str(alert.id), "token_call_id": str(token.id)},
            )
        )

    async def _queue_early_candidate_x2(
        self,
        session: AsyncSession,
        token: TokenCallRecord,
    ) -> bool:
        """Queue the independent call-only signal before legacy market/whale evaluation."""

        if (
            not self.settings.early_candidate_x2_enabled
            or self.settings.alerts_kill_switch
            or not self.settings.early_candidate_x2_chat_ids
        ):
            return False
        blacklist = await session.scalar(
            select(ContractBlacklist).where(
                ContractBlacklist.contract == token.contract,
                ContractBlacklist.active.is_(True),
            )
        )
        if blacklist is not None:
            return False
        domain_token = _domain_token(token)
        evaluation = evaluate_early_candidate_x2(domain_token)
        if not evaluation.matched:
            return False
        evaluated_at = datetime.now(tz=UTC)
        score = ScoreEvaluationRecord(
            token_call_id=token.id,
            market_snapshot_id=None,
            rule_version=evaluation.rule_version,
            evaluated_at=evaluated_at,
            score=100,
            decision=EARLY_CANDIDATE_X2_LEVEL,
            hard_reject=False,
            components=[],
            hard_checks=[check.as_record() for check in evaluation.checks],
            structures=["INITIAL_CALL_ONLY", "NO_WHALE_DATA", "EARLY_CANDIDATE_X2"],
            input_hash=evaluation.input_hash,
            shadow=False,
        )
        session.add(score)
        await session.flush()
        SCORE_EVALUATIONS.labels(EARLY_CANDIDATE_X2_LEVEL, evaluation.rule_version).inc()
        text = render_early_candidate_x2(domain_token, evaluation)
        for chat_id in set(self.settings.early_candidate_x2_chat_ids):
            await self._queue_alert(
                session,
                token,
                score,
                chat_id,
                EARLY_CANDIDATE_X2_LEVEL,
                text,
                topic="TELEGRAM_EARLY_X2_ALERT",
            )
        return True

    async def _audit(
        self,
        session: AsyncSession,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str | None,
        reason: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        session.add(
            AuditLog(
                actor=actor,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                reason=reason,
                before=before,
                after=after,
            )
        )


def _optional_decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _time_in_window(value: str, start: str, end: str) -> bool:
    if start <= end:
        return start <= value < end
    return value >= start or value < end


def _domain_token(record: TokenCallRecord) -> TokenCall:
    return TokenCall(
        contract=record.contract,
        ticker=record.ticker,
        token_name=record.token_name,
        call_ts=record.call_ts,
        source_id=str(record.source_id),
        source_message_id=record.source_message_id,
        call_mc_usd=record.call_mc_usd,
        age_seconds=record.age_seconds,
        holders=record.holders,
        top10_pct=record.top10_pct,
        bundled_pct=record.bundled_pct,
        first50_pct=record.first50_pct,
        jeeters_pct=record.jeeters_pct,
        quickflip_pct=record.quickflip_pct,
        snipers_pct=record.snipers_pct,
        insiders_pct=record.insiders_pct,
        dev_pct=record.dev_pct,
        safe_pct=record.safe_pct,
        poor_pct=record.poor_pct,
        rule_version=record.rule_version,
        dataset_split=DatasetSplit(record.dataset_split),
    )


def _domain_whale(record: WhaleEventRecord) -> WhaleEvent:
    return WhaleEvent(
        label=record.whale_label,
        normalized_label=record.normalized_label,
        event_ts=record.event_ts,
        sol_amount=record.sol_amount,
        whale_mc_usd=record.whale_mc_usd,
        wallet_cluster=record.wallet_cluster,
        signature=record.signature,
        verification_status=VerificationStatus(record.verification_status),
        source_id=str(record.source_id),
        source_message_id=record.source_message_id,
    )


def _domain_multiple(record: MultipleEventRecord) -> MultipleEvent:
    return MultipleEvent(
        multiple=record.multiple,
        event_ts=record.event_ts,
        peak_mc_usd=record.peak_mc_usd,
        elapsed_seconds=record.elapsed_seconds,
    )
