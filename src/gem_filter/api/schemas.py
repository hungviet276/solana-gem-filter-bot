from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TelegramUpdateIn(BaseModel):
    source_channel_id: int
    source_message_id: int
    reply_to_message_id: int | None = None
    event_ts: datetime
    raw_text: str = Field(min_length=1, max_length=100_000)
    edited: bool = False
    revision: int = Field(default=0, ge=0)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_ts")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("event_ts must include timezone")
        return value


class LoginIn(BaseModel):
    email: str
    password: str


class RuleCreateIn(BaseModel):
    version: str = Field(pattern=r"^[A-Z0-9_.-]{3,64}$")
    config: dict[str, Any]
    shadow: bool = False


class TokenSimulationIn(BaseModel):
    contract: str
    ticker: str
    token_name: str | None = None
    call_ts: datetime
    call_mc_usd: Decimal | None = None
    age_seconds: int | None = None
    holders: int | None = None
    top10_pct: Decimal | None = None
    bundled_pct: Decimal | None = None
    first50_pct: Decimal | None = None
    jeeters_pct: Decimal | None = None
    quickflip_pct: Decimal | None = None
    snipers_pct: Decimal | None = None
    insiders_pct: Decimal | None = None
    dev_pct: Decimal | None = None
    safe_pct: Decimal | None = None
    poor_pct: Decimal | None = None


class MarketSimulationIn(BaseModel):
    provider_ts: datetime
    market_cap_usd: Decimal | None = None
    fdv_usd: Decimal | None = None
    liquidity_usd: Decimal | None = None
    price_usd: Decimal | None = None


class WhaleSimulationIn(BaseModel):
    label: str
    normalized_label: str
    event_ts: datetime
    sol_amount: Decimal | None = None
    whale_mc_usd: Decimal | None = None
    wallet_cluster: str | None = None


class MultipleSimulationIn(BaseModel):
    multiple: int = Field(ge=2, le=99)
    event_ts: datetime
    peak_mc_usd: Decimal | None = None
    elapsed_seconds: int | None = None


class SimulationIn(BaseModel):
    token: TokenSimulationIn
    market: MarketSimulationIn | None = None
    whales: list[WhaleSimulationIn] = Field(default_factory=list)
    outcomes: list[MultipleSimulationIn] = Field(default_factory=list)
    evaluation_time: datetime


class SourceCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    channel_id: int
    connection_mode: str = Field(pattern=r"^(bot_api|mtproto|mock)$")
    parser_version: str = "gemtools-parser-1.0.0"
    enabled: bool = True


class SourceUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    connection_mode: str | None = Field(default=None, pattern=r"^(bot_api|mtproto|mock)$")
    parser_version: str | None = None
    enabled: bool | None = None


class SubscriptionIn(BaseModel):
    destination_chat_id: str
    levels: list[str] = Field(default_factory=lambda: ["EARLY", "STRONG"])
    minimum_score: int = Field(default=0, ge=0, le=100)
    quiet_hours: dict[str, Any] = Field(default_factory=dict)
    source_ids: list[str] = Field(default_factory=list)
    enabled: bool = True


class WhaleProfileIn(BaseModel):
    label: str
    aliases: list[str] = Field(default_factory=list)
    wallets: list[str] = Field(default_factory=list)
    cluster_id: str
    trust_score: Decimal | None = Field(default=None, ge=0, le=1)
    observed_samples: int = Field(default=0, ge=0)
    active: bool = True


class CloseTokenIn(BaseModel):
    reason: str = Field(min_length=3, max_length=255)


class BlacklistIn(BaseModel):
    contract: str = Field(pattern=r"^[1-9A-HJ-NP-Za-km-z]{32,64}$")
    reason: str = Field(min_length=3, max_length=255)


class BacktestCreateIn(BaseModel):
    rule_version: str
    dataset_split: str = Field(pattern=r"^(IN_SAMPLE|VALIDATION|OUT_OF_SAMPLE)$")
    date_from: datetime | None = None
    date_to: datetime | None = None


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
