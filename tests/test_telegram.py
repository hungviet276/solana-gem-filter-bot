from __future__ import annotations

import json

import httpx
import pytest

from gem_filter.infrastructure.telegram import TelegramAlertClient, TelegramAPIError


@pytest.mark.asyncio
async def test_alert_includes_research_links_for_contract() -> None:
    contract = "57NcABSX9VWADXUrijtQcK7Dh8khZJa3Er2xVKA2pump"

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        assert request.url.path.endswith("/sendMessage")
        research_buttons = payload["reply_markup"]["inline_keyboard"][1]
        assert research_buttons == [
            {
                "text": "Search X/Twitter",
                "url": f"https://x.com/search?q={contract}&f=live",
            },
            {
                "text": "Check Mevx",
                "url": f"https://mevx.io/solana/{contract}",
            },
        ]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 123}})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    telegram = TelegramAlertClient("test-token", client=http_client)

    message_id = await telegram.send_message(
        "-100123",
        "alert",
        contract=contract,
        chart_url="https://dexscreener.com/solana/pair",
    )

    await http_client.aclose()
    assert message_id == 123


@pytest.mark.asyncio
async def test_telegram_error_exposes_description_without_token_or_url() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: can't parse entities",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    telegram = TelegramAlertClient("super-secret-token", client=http_client)

    with pytest.raises(TelegramAPIError) as captured:
        await telegram.send_message("-100123", "bad", contract="ABC123")

    await http_client.aclose()
    message = str(captured.value)
    assert "can't parse entities" in message
    assert "super-secret-token" not in message
    assert "api.telegram.org" not in message
