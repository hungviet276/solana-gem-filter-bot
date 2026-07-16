from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

import httpx
from telethon import TelegramClient, events

from gem_filter.config import Settings, get_settings
from gem_filter.infrastructure.database import create_engine_and_session
from gem_filter.infrastructure.runtime_store import PostgresRuntimeStore
from gem_filter.infrastructure.telegram import TelegramBotReaderClient
from gem_filter.observability import configure_logging

logger = logging.getLogger(__name__)


class IngestionPublisher:
    def __init__(self, settings: Settings) -> None:
        self.url = settings.ingestion_api_url
        self.token = settings.internal_api_token.get_secret_value()
        self.client = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        await self.client.aclose()

    async def publish(self, envelope: dict[str, Any]) -> None:
        response = await self.client.post(
            self.url, json=envelope, headers={"X-Internal-Token": self.token}
        )
        response.raise_for_status()


async def run_bot_api(
    settings: Settings, publisher: IngestionPublisher, runtime_store: PostgresRuntimeStore
) -> None:
    if settings.telegram_bot_token is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required for bot_api mode")
    reader = TelegramBotReaderClient(settings.telegram_bot_token.get_secret_value())
    try:
        offset = await runtime_store.get_offset()
        while True:
            try:
                updates = await reader.get_updates(offset, poll_timeout=30)
                for update in updates:
                    update_id = int(update["update_id"])
                    message = update.get("channel_post") or update.get("edited_channel_post")
                    if message:
                        channel_id = int(message["chat"]["id"])
                        if not settings.source_id_set or channel_id in settings.source_id_set:
                            edited = "edited_channel_post" in update
                            await publisher.publish(
                                {
                                    "source_channel_id": channel_id,
                                    "source_message_id": int(message["message_id"]),
                                    "reply_to_message_id": (
                                        message.get("reply_to_message") or {}
                                    ).get("message_id"),
                                    "event_ts": datetime.fromtimestamp(
                                        message.get("edit_date") or message["date"], tz=UTC
                                    ).isoformat(),
                                    "raw_text": message.get("text")
                                    or message.get("caption")
                                    or "[service message]",
                                    "edited": edited,
                                    "revision": update_id if edited else 0,
                                    "raw_payload": update,
                                }
                            )
                    offset = update_id + 1
                    await runtime_store.set_offset(offset)
            except (httpx.HTTPError, RuntimeError, KeyError, ValueError):
                logger.exception("bot API reader iteration failed")
                await asyncio.sleep(2)
    finally:
        await reader.close()


async def run_mtproto(settings: Settings, publisher: IngestionPublisher) -> None:
    if settings.telegram_api_id is None or settings.telegram_api_hash is None:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required for mtproto mode")
    client = TelegramClient(
        settings.telegram_session_path,
        settings.telegram_api_id,
        settings.telegram_api_hash.get_secret_value(),
        auto_reconnect=True,
        connection_retries=None,
    )
    chats = list(settings.source_id_set) or None

    async def publish_event(event: Any, *, edited: bool) -> None:
        message = event.message
        await publisher.publish(
            {
                "source_channel_id": int(event.chat_id),
                "source_message_id": int(message.id),
                "reply_to_message_id": message.reply_to_msg_id,
                "event_ts": message.date.astimezone(UTC).isoformat(),
                "raw_text": message.raw_text or "[service message]",
                "edited": edited,
                "revision": int(message.edit_date.timestamp())
                if edited and message.edit_date
                else 0,
                "raw_payload": {
                    "mtproto": True,
                    "post": bool(message.post),
                    "views": message.views,
                },
            }
        )

    @client.on(events.NewMessage(chats=chats, incoming=True))
    async def on_new(event: Any) -> None:
        await publish_event(event, edited=False)

    @client.on(events.MessageEdited(chats=chats, incoming=True))
    async def on_edit(event: Any) -> None:
        await publish_event(event, edited=True)

    # This process has no code path that sends, edits, deletes or reacts to source messages.
    await client.start()
    await client.run_until_disconnected()


async def run_mock(settings: Settings, publisher: IngestionPublisher) -> None:
    logger.info("mock reader expects one TelegramUpdateIn JSON object per stdin line")
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            await asyncio.sleep(1)
            continue
        await publisher.publish(json.loads(line))


async def async_main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    publisher = IngestionPublisher(settings)
    engine = None
    try:
        if settings.telegram_source_mode == "bot_api":
            engine, sessions = create_engine_and_session(settings)
            await run_bot_api(settings, publisher, PostgresRuntimeStore(sessions))
        elif settings.telegram_source_mode == "mtproto":
            await run_mtproto(settings, publisher)
        else:
            await run_mock(settings, publisher)
    finally:
        await publisher.close()
        if engine is not None:
            await engine.dispose()


def run() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    run()
