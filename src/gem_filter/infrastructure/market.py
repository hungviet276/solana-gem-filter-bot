from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from gem_filter.config import Settings
from gem_filter.domain.models import MarketSnapshot
from gem_filter.observability import MARKET_ERRORS, MARKET_LATENCY


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


class DexScreenerMarketProvider:
    """Read-only DEX Screener adapter; it never submits a transaction."""

    name = "dexscreener"

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        allowed_quotes: set[str],
        dex_priority: tuple[str, ...] = ("pumpfun", "raydium", "meteora"),
        client: httpx.AsyncClient | None = None,
        name: str = "dexscreener",
    ) -> None:
        self.name = name
        self._base_url = base_url.rstrip("/")
        self._allowed_quotes = allowed_quotes
        self._dex_priority = {name.casefold(): index for index, name in enumerate(dex_priority)}
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout_seconds, headers={"Accept": "application/json"}
        )

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def fetch(
        self, contract: str, *, retries: int = 2
    ) -> tuple[MarketSnapshot, dict[str, Any]]:
        url = f"{self._base_url}/token-pairs/v1/solana/{contract}"
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            started = asyncio.get_running_loop().time()
            try:
                response = await self._client.get(url)
                MARKET_LATENCY.labels(self.name).observe(
                    asyncio.get_running_loop().time() - started
                )
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, list):
                    raise ValueError("DEX Screener token-pairs response must be an array")
                pair = self._select_pair(body, contract)
                now = datetime.now(tz=UTC)
                txns_5m = (
                    pair.get("txns", {}).get("m5", {}) if isinstance(pair.get("txns"), dict) else {}
                )
                snapshot = MarketSnapshot(
                    captured_at=now,
                    provider_ts=now,
                    provider=self.name,
                    pair_address=pair.get("pairAddress"),
                    dex=pair.get("dexId"),
                    market_cap_usd=_decimal(pair.get("marketCap")),
                    fdv_usd=_decimal(pair.get("fdv")),
                    liquidity_usd=_decimal((pair.get("liquidity") or {}).get("usd")),
                    price_usd=_decimal(pair.get("priceUsd")),
                    volume_24h_usd=_decimal((pair.get("volume") or {}).get("h24")),
                    buys_5m=int(txns_5m["buys"]) if txns_5m.get("buys") is not None else None,
                    sells_5m=int(txns_5m["sells"]) if txns_5m.get("sells") is not None else None,
                    selection_reason="highest_liquidity_allowed_quote_then_dex_priority",
                )
                return snapshot, pair
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                last_error = exc
                MARKET_ERRORS.labels(self.name, type(exc).__name__).inc()
                if attempt < retries:
                    await asyncio.sleep(0.25 * (2**attempt))
        raise RuntimeError(f"market lookup failed for {contract}") from last_error

    def _select_pair(self, pairs: list[dict[str, Any]], contract: str) -> dict[str, Any]:
        candidates = []
        for pair in pairs:
            if str(pair.get("chainId", "")).casefold() != "solana":
                continue
            base_address = str((pair.get("baseToken") or {}).get("address", ""))
            quote_address = str((pair.get("quoteToken") or {}).get("address", ""))
            if contract not in {base_address, quote_address}:
                continue
            quote_symbol = str((pair.get("quoteToken") or {}).get("symbol", "")).upper()
            if self._allowed_quotes and quote_symbol not in self._allowed_quotes:
                continue
            candidates.append(pair)
        if not candidates:
            raise ValueError("no eligible Solana pool for contract")

        def key(pair: dict[str, Any]) -> tuple[Decimal, int]:
            liquidity = _decimal((pair.get("liquidity") or {}).get("usd")) or Decimal("0")
            priority = -self._dex_priority.get(str(pair.get("dexId", "")).casefold(), 999)
            return liquidity, priority

        return max(candidates, key=key)


class StaticMarketProvider:
    """Deterministic test/dev adapter."""

    def __init__(self, snapshot: MarketSnapshot) -> None:
        self.snapshot = snapshot

    async def fetch(
        self, contract: str, *, retries: int = 0
    ) -> tuple[MarketSnapshot, dict[str, Any]]:
        del contract, retries
        return self.snapshot, {"provider": "static"}

    async def close(self) -> None:
        return None


class FallbackMarketProvider:
    def __init__(self, providers: tuple[DexScreenerMarketProvider, ...]) -> None:
        if not providers:
            raise ValueError("at least one market provider is required")
        self.providers = providers

    async def fetch(
        self, contract: str, *, retries: int = 2
    ) -> tuple[MarketSnapshot, dict[str, Any]]:
        last_error: Exception | None = None
        for provider in self.providers:
            try:
                return await provider.fetch(contract, retries=retries)
            except Exception as exc:
                last_error = exc
        raise RuntimeError("all market providers failed") from last_error

    async def close(self) -> None:
        for provider in self.providers:
            await provider.close()


def create_market_provider(settings: Settings) -> FallbackMarketProvider:
    providers = [
        DexScreenerMarketProvider(
            settings.market_provider_base_url,
            timeout_seconds=settings.market_provider_timeout_seconds,
            allowed_quotes=settings.allowed_quotes,
            name="dexscreener_primary",
        )
    ]
    if settings.market_fallback_base_url:
        providers.append(
            DexScreenerMarketProvider(
                settings.market_fallback_base_url,
                timeout_seconds=settings.market_provider_timeout_seconds,
                allowed_quotes=settings.allowed_quotes,
                name="dexscreener_fallback",
            )
        )
    return FallbackMarketProvider(tuple(providers))
