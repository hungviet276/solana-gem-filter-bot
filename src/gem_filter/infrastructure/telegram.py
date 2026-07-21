from __future__ import annotations

from typing import Any, cast
from urllib.parse import quote, urlencode

import httpx


class TelegramAPIError(RuntimeError):
    """A deterministic Telegram Bot API rejection with a token-safe message."""

    def __init__(
        self,
        method: str,
        status_code: int,
        error_code: int | None,
        description: str,
    ) -> None:
        self.method = method
        self.status_code = status_code
        self.error_code = error_code
        self.description = description
        code = error_code if error_code is not None else status_code
        super().__init__(f"Telegram {method} failed ({code}): {description}")


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
                },
                {
                    "text": "Check Mevx",
                    "url": f"https://mevx.io/solana/{quote(contract, safe='')}",
                },
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
        payload = _telegram_payload(response, "sendMessage")
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
        _telegram_payload(response, "editMessageText")


def _telegram_payload(response: httpx.Response, method: str) -> dict[str, Any]:
    try:
        payload = cast(dict[str, Any], response.json())
    except ValueError as exc:
        if response.is_error:
            raise TelegramAPIError(
                method,
                response.status_code,
                None,
                "Telegram returned a non-JSON error response",
            ) from exc
        raise RuntimeError(f"Telegram {method} returned invalid JSON") from exc
    if response.is_error or not payload.get("ok"):
        raw_error_code = payload.get("error_code")
        error_code = raw_error_code if isinstance(raw_error_code, int) else None
        description = str(payload.get("description", "unknown Telegram error"))
        raise TelegramAPIError(method, response.status_code, error_code, description)
    return payload


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
