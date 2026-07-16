from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from gem_filter.application.processor import ProcessingService
from gem_filter.config import get_settings
from gem_filter.infrastructure.database import (
    AlertRecord,
    MarketSnapshotRecord,
    MultipleEventRecord,
    OutboxEvent,
    RawTelegramEvent,
    TokenCallRecord,
    TokenStateHistory,
    WhaleEventRecord,
    create_engine_and_session,
)
from gem_filter.infrastructure.market import create_market_provider
from gem_filter.observability import ORPHAN_EVENTS, configure_logging

logger = logging.getLogger(__name__)
WINDOWS = ((300, "T5"), (900, "T15"), (1800, "T30"), (3600, "T60"))
ACTIVE = {"VERIFYING", "WATCH", "EARLY", "STRONG"}


class Scheduler:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.engine, self.sessions = create_engine_and_session(self.settings)
        self.market = create_market_provider(self.settings)
        self.processor = ProcessingService(self.sessions, self.market, self.settings)

    async def close(self) -> None:
        await self.market.close()
        await self.engine.dispose()

    async def tick(self) -> None:
        now = datetime.now(tz=UTC)
        await self._expire(now)
        await self._correlate_orphans()
        await self._scheduled_snapshots(now)
        await self._recover_outbox(now)

    async def _expire(self, now: datetime) -> None:
        async with self.sessions() as session, session.begin():
            rows = (
                await session.scalars(
                    select(TokenCallRecord)
                    .where(TokenCallRecord.status.in_(ACTIVE), TokenCallRecord.expires_at <= now)
                    .with_for_update(skip_locked=True)
                    .limit(500)
                )
            ).all()
            for token in rows:
                previous = token.status
                token.status = "EXPIRED"
                token.state_reason = "ENTRY_WINDOW_EXPIRED"
                session.add(
                    TokenStateHistory(
                        token_call_id=token.id,
                        from_status=previous,
                        to_status="EXPIRED",
                        reason="ENTRY_WINDOW_EXPIRED",
                    )
                )

    async def _correlate_orphans(self) -> None:
        tokens_to_score: set[uuid.UUID] = set()
        async with self.sessions() as session, session.begin():
            whales = (
                await session.scalars(
                    select(WhaleEventRecord)
                    .where(WhaleEventRecord.token_call_id.is_(None))
                    .with_for_update(skip_locked=True)
                    .limit(200)
                )
            ).all()
            multiples = (
                await session.scalars(
                    select(MultipleEventRecord)
                    .where(MultipleEventRecord.token_call_id.is_(None))
                    .with_for_update(skip_locked=True)
                    .limit(200)
                )
            ).all()
            for event in [*whales, *multiples]:
                raw = await session.get(RawTelegramEvent, event.raw_event_id)
                if raw is None or raw.reply_to_message_id is None:
                    continue
                token = await session.scalar(
                    select(TokenCallRecord).where(
                        TokenCallRecord.source_id == raw.source_id,
                        TokenCallRecord.source_message_id == raw.reply_to_message_id,
                    )
                )
                if token:
                    event.token_call_id = token.id
                    event.orphan_reason = None
                    tokens_to_score.add(token.id)
            remaining = await session.scalar(
                select(func.count())
                .select_from(WhaleEventRecord)
                .where(WhaleEventRecord.token_call_id.is_(None))
            )
            remaining += await session.scalar(
                select(func.count())
                .select_from(MultipleEventRecord)
                .where(MultipleEventRecord.token_call_id.is_(None))
            )
            ORPHAN_EVENTS.set(remaining)
        for token_id in tokens_to_score:
            try:
                await self.processor.scheduled_evaluate(token_id, "ORPHAN_CORRELATED")
            except (LookupError, ValueError):
                continue

    async def _scheduled_snapshots(self, now: datetime) -> None:
        due: list[tuple[uuid.UUID, str]] = []
        async with self.sessions() as session:
            tokens = (
                await session.scalars(
                    select(TokenCallRecord)
                    .where(TokenCallRecord.status.in_(ACTIVE))
                    .order_by(TokenCallRecord.call_ts)
                    .limit(1000)
                )
            ).all()
            for token in tokens:
                elapsed = (now - token.call_ts).total_seconds()
                for seconds, reason in WINDOWS:
                    if elapsed < seconds:
                        continue
                    exists = await session.scalar(
                        select(MarketSnapshotRecord.id).where(
                            MarketSnapshotRecord.token_call_id == token.id,
                            MarketSnapshotRecord.capture_reason == reason,
                        )
                    )
                    if exists is None:
                        due.append((token.id, reason))
        semaphore = asyncio.Semaphore(5)

        async def evaluate_one(token_id: uuid.UUID, reason: str) -> None:
            async with semaphore:
                try:
                    await self.processor.scheduled_evaluate(token_id, reason)
                except (LookupError, ValueError):
                    return
                except Exception:
                    logger.exception(
                        "scheduled evaluation failed", extra={"token_call_id": str(token_id)}
                    )

        await asyncio.gather(*(evaluate_one(token_id, reason) for token_id, reason in due[:200]))

    async def _recover_outbox(self, now: datetime) -> None:
        stale_before = now - timedelta(minutes=5)
        async with self.sessions() as session, session.begin():
            rows = (
                await session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.status == "PROCESSING", OutboxEvent.claimed_at < stale_before
                    )
                    .with_for_update(skip_locked=True)
                    .limit(500)
                )
            ).all()
            for row in rows:
                if row.topic == "TELEGRAM_ALERT":
                    alert_id = row.payload.get("alert_id")
                    alert = (
                        await session.get(AlertRecord, uuid.UUID(alert_id)) if alert_id else None
                    )
                    if alert and alert.status == "SENDING":
                        alert.status = "MANUAL_REVIEW"
                        alert.error = "Worker died during ambiguous Telegram delivery"
                        row.status = "DEAD"
                        row.last_error = (
                            "Ambiguous external delivery; manual reconciliation required"
                        )
                        continue
                row.status = "PENDING"
                row.claimed_at = None
                row.available_at = now

    async def run_forever(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(10)


async def async_main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    scheduler = Scheduler()
    try:
        await scheduler.run_forever()
    finally:
        await scheduler.close()


def run() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    run()
