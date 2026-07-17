from __future__ import annotations

import html
from typing import Any
from urllib.parse import urlencode

import httpx


class TelegramAlertClient:
    def __init__(self, bot_token: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        contract: str,
        chart_url: str | None = None,
        details_url: str | None = None,
    ) -> int:
        keyboard: list[list[dict[str, Any]]] = []
        row: list[dict[str, Any]] = [{"text": "Copy contract", "copy_text": {"text": contract}}]
        if chart_url:
            row.append({"text": "Chart", "url": chart_url})
        keyboard.append(row)
        keyboard.append(
            [
                {
                    "text": "Search X/Twitter",
                    "url": f"https://x.com/search?{urlencode({'q': contract, 'f': 'live'})}",
                }
            ]
        )
        if details_url:
            keyboard.append([{"text": "Chi tiết", "url": details_url}])
        response = await self._client.post(
            f"{self._base_url}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": keyboard},
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            description = html.escape(str(payload.get("description", "unknown error")))
            raise RuntimeError(f"Telegram sendMessage failed: {description}")
        return int(payload["result"]["message_id"])

    async def edit_message(self, chat_id: str, message_id: int, text: str) -> None:
        response = await self._client.post(
            f"{self._base_url}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        response.raise_for_status()


class TelegramBotReaderClient:
    """Long-polling source reader. This class exposes no send/edit/delete methods."""

    def __init__(self, bot_token: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(timeout=40.0)

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def get_updates(self, offset: int | None, poll_timeout: int = 30) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": poll_timeout,
            "allowed_updates": '["channel_post","edited_channel_post"]',
        }
        if offset is not None:
            params["offset"] = offset
        response = await self._client.get(self._url, params=params)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("description", "getUpdates failed")))
        return list(payload.get("result", []))
