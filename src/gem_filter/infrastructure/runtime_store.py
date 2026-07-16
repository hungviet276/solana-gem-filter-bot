from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gem_filter.infrastructure.database import RuntimeState

OFFSET_KEY = "reader:bot_api:offset"


def dedupe_key(namespace: str, event_id: str) -> str:
    return f"dedupe:{namespace}:{event_id}"[:255]


def rate_key(scope: str, now: datetime) -> str:
    minute = now.astimezone(UTC).replace(second=0, microsecond=0)
    return f"rate:{scope}:{minute:%Y%m%d%H%M}"[:255]


class PostgresRuntimeStore:
    """Atomic offset, TTL dedupe and rate-limit state backed by PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def get_offset(self) -> int | None:
        async with self.sessions() as session:
            value = await session.scalar(
                select(RuntimeState.string_value).where(RuntimeState.key == OFFSET_KEY)
            )
        return int(value) if value is not None else None

    async def set_offset(self, offset: int) -> None:
        statement = (
            insert(RuntimeState)
            .values(key=OFFSET_KEY, string_value=str(offset), counter_value=0)
            .on_conflict_do_update(
                index_elements=[RuntimeState.key],
                set_={
                    "string_value": str(offset),
                    "counter_value": 0,
                    "expires_at": None,
                    "updated_at": datetime.now(tz=UTC),
                },
            )
        )
        async with self.sessions() as session, session.begin():
            await session.execute(statement)

    async def claim_once(
        self,
        namespace: str,
        event_id: str,
        ttl_seconds: int,
        *,
        session: AsyncSession | None = None,
    ) -> bool:
        now = datetime.now(tz=UTC)
        key = dedupe_key(namespace, event_id)
        statement = (
            insert(RuntimeState)
            .values(
                key=key,
                string_value="1",
                counter_value=0,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
            .on_conflict_do_update(
                index_elements=[RuntimeState.key],
                set_={
                    "string_value": "1",
                    "counter_value": 0,
                    "expires_at": now + timedelta(seconds=ttl_seconds),
                    "updated_at": now,
                },
                where=RuntimeState.expires_at.is_(None) | (RuntimeState.expires_at <= now),
            )
            .returning(RuntimeState.key)
        )
        if session is not None:
            result = await session.execute(statement)
            claimed = result.scalar_one_or_none() is not None
            await self._prune(session, now)
            return claimed
        async with self.sessions() as owned_session, owned_session.begin():
            result = await owned_session.execute(statement)
            claimed = result.scalar_one_or_none() is not None
            await self._prune(owned_session, now)
            return claimed

    async def allow(self, scope: str, limit_per_minute: int) -> bool:
        now = datetime.now(tz=UTC)
        key = rate_key(scope, now)
        statement = (
            insert(RuntimeState)
            .values(
                key=key,
                counter_value=1,
                expires_at=now + timedelta(seconds=90),
            )
            .on_conflict_do_update(
                index_elements=[RuntimeState.key],
                set_={
                    "counter_value": RuntimeState.counter_value + 1,
                    "expires_at": now + timedelta(seconds=90),
                    "updated_at": now,
                },
            )
            .returning(RuntimeState.counter_value)
        )
        async with self.sessions() as session, session.begin():
            value = (await session.execute(statement)).scalar_one()
            await self._prune(session, now)
        return int(value) <= limit_per_minute

    @staticmethod
    async def _prune(session: AsyncSession, now: datetime) -> None:
        await session.execute(
            delete(RuntimeState).where(
                RuntimeState.expires_at.is_not(None),
                RuntimeState.expires_at < now - timedelta(days=1),
            )
        )
