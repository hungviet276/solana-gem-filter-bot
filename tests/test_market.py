from __future__ import annotations

from datetime import UTC

import httpx
import pytest

from gem_filter.infrastructure.market import DexScreenerMarketProvider

CONTRACT = "A" * 44


@pytest.mark.asyncio
async def test_market_selects_highest_liquidity_allowed_quote() -> None:
    pairs = [
        {
            "chainId": "solana",
            "dexId": "raydium",
            "pairAddress": "low",
            "baseToken": {"address": CONTRACT, "symbol": "GEM"},
            "quoteToken": {"address": "SOL", "symbol": "SOL"},
            "liquidity": {"usd": 10000},
            "marketCap": 40000,
            "fdv": 40000,
            "priceUsd": "0.001",
            "volume": {"h24": 5000},
            "txns": {"m5": {"buys": 5, "sells": 2}},
        },
        {
            "chainId": "solana",
            "dexId": "meteora",
            "pairAddress": "high",
            "baseToken": {"address": CONTRACT, "symbol": "GEM"},
            "quoteToken": {"address": "USDC", "symbol": "USDC"},
            "liquidity": {"usd": 25000},
            "marketCap": 42000,
            "fdv": 42000,
            "priceUsd": "0.0011",
            "volume": {"h24": 7000},
            "txns": {"m5": {"buys": 8, "sells": 3}},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(CONTRACT)
        return httpx.Response(200, json=pairs)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = DexScreenerMarketProvider(
        "https://api.example",
        timeout_seconds=1,
        allowed_quotes={"SOL", "USDC"},
        client=client,
    )
    snapshot, raw = await provider.fetch(CONTRACT)
    await client.aclose()
    assert snapshot.pair_address == "high"
    assert snapshot.market_cap_usd == 42000
    assert snapshot.provider_ts.tzinfo is UTC
    assert raw["dexId"] == "meteora"


@pytest.mark.asyncio
async def test_market_rejects_ineligible_pool() -> None:
    pair = {
        "chainId": "ethereum",
        "pairAddress": "wrong-chain",
        "baseToken": {"address": CONTRACT},
        "quoteToken": {"symbol": "WETH"},
    }
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[pair]))
    )
    provider = DexScreenerMarketProvider(
        "https://api.example",
        timeout_seconds=1,
        allowed_quotes={"SOL"},
        client=client,
    )
    with pytest.raises(RuntimeError):
        await provider.fetch(CONTRACT, retries=0)
    await client.aclose()
