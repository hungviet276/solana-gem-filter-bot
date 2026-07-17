from __future__ import annotations

import httpx
import pytest

from gem_filter.infrastructure.telegram import TelegramAlertClient


@pytest.mark.asyncio
async def test_alert_includes_latest_x_search_for_contract() -> None:
    contract = "57NcABSX9VWADXUrijtQcK7Dh8khZJa3Er2xVKA2pump"

    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read()
        assert request.url.path.endswith("/sendMessage")
        assert b'"text":"Search X/Twitter"' in payload
        assert b'"url":"https://x.com/search?q=' + contract.encode() + b'&f=live"' in payload
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
