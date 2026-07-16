from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from gem_filter.application.backtest import ReplayCase, run_backtest
from gem_filter.application.processor import (
    ProcessingService,
    _domain_multiple,
    _domain_token,
    _domain_whale,
    _rule_from_payload,
)
from gem_filter.config import get_settings
from gem_filter.domain.onchain import verify_enhanced_swap_buy
from gem_filter.domain.parser import parse_message
from gem_filter.infrastructure.database import (
    AlertRecord,
    AuditLog,
    BacktestRun,
    MarketSnapshotRecord,
    MultipleEventRecord,
    OutboxEvent,
    ParsedMessage,
    RawTelegramEvent,
    RuleVersion,
    TokenCallRecord,
    WhaleEventRecord,
    WhaleProfile,
    create_engine_and_session,
)
from gem_filter.infrastructure.market import create_market_provider
from gem_filter.infrastructure.runtime_store import PostgresRuntimeStore
from gem_filter.infrastructure.telegram import TelegramAlertClient
from gem_filter.observability import ALERTS, configure_logging

logger = logging.getLogger(__name__)


class RetryAlert(RuntimeError):
    pass


class Worker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.engine, self.sessions = create_engine_and_session(self.settings)
        self.market = create_market_provider(self.settings)
        self.processor = ProcessingService(self.sessions, self.market, self.settings)
        self.alert_client = (
            TelegramAlertClient(self.settings.alert_bot_token.get_secret_value())
            if self.settings.alert_bot_token
            else None
        )
        self.runtime_store = PostgresRuntimeStore(self.sessions)

    async def close(self) -> None:
        if self.alert_client:
            await self.alert_client.close()
        await self.market.close()
        await self.engine.dispose()

    async def claim(self) -> dict[str, Any] | None:
        async with self.sessions() as session, session.begin():
            row = await session.scalar(
                select(OutboxEvent)
                .where(
                    OutboxEvent.status == "PENDING",
                    OutboxEvent.available_at <= datetime.now(tz=UTC),
                )
                .order_by(OutboxEvent.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            row.status = "PROCESSING"
            row.attempts += 1
            row.claimed_at = datetime.now(tz=UTC)
            return {
                "id": row.id,
                "topic": row.topic,
                "payload": row.payload,
                "attempts": row.attempts,
            }

    async def complete(self, event_id: uuid.UUID) -> None:
        async with self.sessions() as session, session.begin():
            row = await session.get(OutboxEvent, event_id, with_for_update=True)
            if row:
                row.status = "DONE"
                row.processed_at = datetime.now(tz=UTC)
                row.claimed_at = None

    async def fail(
        self, event_id: uuid.UUID, error: Exception, attempts: int, *, ambiguous: bool = False
    ) -> None:
        async with self.sessions() as session, session.begin():
            row = await session.get(OutboxEvent, event_id, with_for_update=True)
            if row is None:
                return
            row.last_error = f"{type(error).__name__}: {error}"[:2000]
            row.claimed_at = None
            if ambiguous or attempts >= 5:
                row.status = "DEAD"
            else:
                row.status = "PENDING"
                row.available_at = datetime.now(tz=UTC) + timedelta(seconds=min(300, 2**attempts))

    async def handle(self, item: dict[str, Any]) -> None:
        topic = item["topic"]
        payload = item["payload"]
        if topic == "PROCESS_RAW_EVENT":
            await self.processor.process_raw(uuid.UUID(payload["raw_event_id"]))
        elif topic == "TELEGRAM_ALERT":
            await self._send_alert(uuid.UUID(payload["alert_id"]))
        elif topic == "BACKTEST":
            await self._backtest(uuid.UUID(payload["backtest_run_id"]))
        elif topic == "HELIUS_EVENT":
            await self._helius(payload)
        elif topic == "REPARSE_RAW_EVENT":
            await self._reparse(
                uuid.UUID(payload["raw_event_id"]), payload.get("requested_by", "system")
            )
        else:
            raise ValueError(f"unsupported outbox topic: {topic}")

    async def _send_alert(self, alert_id: uuid.UUID) -> None:
        if self.settings.alerts_kill_switch:
            async with self.sessions() as session, session.begin():
                alert = await session.get(AlertRecord, alert_id, with_for_update=True)
                if alert:
                    alert.status = "SUPPRESSED_KILL_SWITCH"
            return
        gate = await self.processor.revalidate_alert(alert_id)
        if gate is None:
            return
        if self.alert_client is None:
            raise RuntimeError("ALERT_BOT_TOKEN is not configured")
        if not await self.runtime_store.allow(
            f"telegram:{gate['chat_id']}", self.settings.alert_rate_per_minute
        ):
            raise RetryAlert("Telegram alert rate limit reached; event will retry")

        async with self.sessions() as session, session.begin():
            alert = await session.get(AlertRecord, alert_id, with_for_update=True)
            if alert is None or alert.status == "SENT":
                return
            alert.status = "SENDING"
        try:
            message_id = await self.alert_client.send_message(
                gate["chat_id"],
                gate["text"],
                contract=gate["contract"],
                chart_url=gate["chart_url"],
                details_url=gate["details_url"],
            )
        except Exception:
            # A timeout after Telegram accepted the request is ambiguous. Do not auto-resend and
            # risk spam; require reconciliation via the operations queue.
            async with self.sessions() as session, session.begin():
                alert = await session.get(AlertRecord, alert_id, with_for_update=True)
                if alert:
                    alert.status = "MANUAL_REVIEW"
                    alert.error = "Ambiguous Telegram delivery; auto-retry disabled"
            raise
        async with self.sessions() as session, session.begin():
            alert = await session.get(AlertRecord, alert_id, with_for_update=True)
            if alert:
                alert.status = "SENT"
                alert.telegram_message_id = message_id
                alert.sent_at = datetime.now(tz=UTC)
                ALERTS.labels(alert.level, "sent").inc()

    async def _backtest(self, run_id: uuid.UUID) -> None:
        async with self.sessions() as session, session.begin():
            run = await session.get(BacktestRun, run_id, with_for_update=True)
            if run is None:
                raise LookupError("backtest run not found")
            run.status = "RUNNING"
            run.started_at = datetime.now(tz=UTC)
            rule_record = await session.scalar(
                select(RuleVersion).where(RuleVersion.version == run.rule_version)
            )
            if rule_record is None:
                raise LookupError("backtest rule not found")
            statement = select(TokenCallRecord).where(
                TokenCallRecord.dataset_split == run.dataset_split
            )
            if run.parameters.get("date_from"):
                statement = statement.where(
                    TokenCallRecord.call_ts >= datetime.fromisoformat(run.parameters["date_from"])
                )
            if run.parameters.get("date_to"):
                statement = statement.where(
                    TokenCallRecord.call_ts <= datetime.fromisoformat(run.parameters["date_to"])
                )
            tokens = (await session.scalars(statement)).all()
            cases: list[ReplayCase] = []
            for token in tokens:
                snapshots = (
                    await session.scalars(
                        select(MarketSnapshotRecord)
                        .where(MarketSnapshotRecord.token_call_id == token.id)
                        .order_by(MarketSnapshotRecord.provider_ts)
                    )
                ).all()
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
                cases.append(
                    ReplayCase(
                        token=_domain_token(token),
                        snapshots=tuple(_snapshot(item) for item in snapshots),
                        whales=tuple(_domain_whale(item) for item in whales),
                        outcomes=tuple(_domain_multiple(item) for item in outcomes),
                    )
                )
            result = run_backtest(
                cases, _rule_from_payload(rule_record.config, rule_record.version)
            )
            run.status = "COMPLETED"
            run.completed_at = datetime.now(tz=UTC)
            run.result = asdict(result)

    async def _helius(self, payload: dict[str, Any]) -> None:
        events = payload.get("events", [])
        if isinstance(events, dict):
            events = [events]
        async with self.sessions() as session, session.begin():
            for event in events:
                signature = event.get("signature") if isinstance(event, dict) else None
                if not signature:
                    continue
                whale = await session.scalar(
                    select(WhaleEventRecord).where(WhaleEventRecord.signature == signature)
                )
                if whale:
                    event_type = str(event.get("type", "")).upper()
                    whale.verification_status = "VERIFIED" if event_type == "SWAP" else "REJECTED"
                    continue
                token_transfers = event.get("tokenTransfers") or []
                wallets = {
                    transfer.get("toUserAccount")
                    for transfer in token_transfers
                    if transfer.get("toUserAccount")
                }
                contracts = {
                    transfer.get("mint") for transfer in token_transfers if transfer.get("mint")
                }
                event_ts_raw = event.get("timestamp")
                event_ts = (
                    datetime.fromtimestamp(event_ts_raw, tz=UTC)
                    if isinstance(event_ts_raw, (int, float))
                    else datetime.now(tz=UTC)
                )
                for wallet in wallets:
                    profile = await session.scalar(
                        select(WhaleProfile).where(
                            WhaleProfile.active.is_(True),
                            WhaleProfile.wallets.contains([wallet]),
                        )
                    )
                    if profile is None:
                        continue
                    for contract in contracts:
                        token = await session.scalar(
                            select(TokenCallRecord)
                            .where(TokenCallRecord.contract == contract)
                            .order_by(TokenCallRecord.call_ts.desc())
                        )
                        if token is None:
                            continue
                        verification = verify_enhanced_swap_buy(
                            event, wallet=wallet, contract=contract
                        )
                        candidate = await session.scalar(
                            select(WhaleEventRecord)
                            .where(
                                WhaleEventRecord.token_call_id == token.id,
                                WhaleEventRecord.verification_status.in_(
                                    ["NOT_REQUESTED", "PENDING", "NOT_FOUND"]
                                ),
                                WhaleEventRecord.event_ts.between(
                                    event_ts - timedelta(minutes=5), event_ts + timedelta(minutes=5)
                                ),
                            )
                            .order_by(WhaleEventRecord.event_ts.desc())
                        )
                        if candidate:
                            candidate.wallet_cluster = profile.cluster_id
                            candidate.signature = signature
                            candidate.verification_status = (
                                "VERIFIED" if verification.verified else "REJECTED"
                            )

    async def _reparse(self, raw_id: uuid.UUID, actor: str) -> None:
        async with self.sessions() as session, session.begin():
            raw = await session.get(RawTelegramEvent, raw_id)
            if raw is None:
                raise LookupError("raw event not found")
            parsed = parse_message(raw.raw_text)
            current = await session.scalar(
                select(ParsedMessage)
                .where(ParsedMessage.raw_event_id == raw.id)
                .order_by(ParsedMessage.created_at.desc())
            )
            before = (
                {
                    "event_type": current.event_type,
                    "payload": current.payload,
                    "warnings": current.warnings,
                }
                if current
                else None
            )
            if current is None:
                session.add(
                    ParsedMessage(
                        raw_event_id=raw.id,
                        parser_version=parsed.parser_version,
                        event_type=parsed.event_type.value,
                        payload=parsed.payload,
                        warnings=list(parsed.warnings),
                        status="PARSED",
                    )
                )
            else:
                current.event_type = parsed.event_type.value
                current.payload = parsed.payload
                current.warnings = list(parsed.warnings)
                current.status = "REPARSED"
            session.add(
                AuditLog(
                    actor=actor,
                    action="RAW_REPARSE",
                    entity_type="raw_event",
                    entity_id=str(raw.id),
                    reason="Manual reparse",
                    before=before,
                    after={
                        "event_type": parsed.event_type.value,
                        "payload": parsed.payload,
                        "warnings": list(parsed.warnings),
                    },
                )
            )

    async def run_forever(self) -> None:
        while True:
            item = await self.claim()
            if item is None:
                await asyncio.sleep(0.25)
                continue
            try:
                await self.handle(item)
            except Exception as exc:
                logger.exception("outbox event failed", extra={"event_id": str(item["id"])})
                ambiguous = item["topic"] == "TELEGRAM_ALERT" and not isinstance(exc, RetryAlert)
                await self.fail(item["id"], exc, item["attempts"], ambiguous=ambiguous)
            else:
                await self.complete(item["id"])


def _snapshot(record: MarketSnapshotRecord):
    from gem_filter.domain.models import MarketSnapshot

    return MarketSnapshot(
        captured_at=record.captured_at,
        provider_ts=record.provider_ts,
        provider=record.provider,
        pair_address=record.pair_address,
        dex=record.dex,
        market_cap_usd=record.market_cap_usd,
        fdv_usd=record.fdv_usd,
        liquidity_usd=record.liquidity_usd,
        price_usd=record.price_usd,
        volume_24h_usd=record.volume_24h_usd,
        buys_5m=record.buys_5m,
        sells_5m=record.sells_5m,
        selection_reason=record.selection_reason,
    )


async def async_main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    worker = Worker()
    try:
        await worker.run_forever()
    finally:
        await worker.close()


def run() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    run()
