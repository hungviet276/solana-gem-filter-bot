from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    CALL = "CALL"
    WHALE_BUY = "WHALE_BUY"
    MULTIPLE_RESULT = "MULTIPLE_RESULT"
    SERVICE = "SERVICE"
    UNKNOWN = "UNKNOWN"


class TokenStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PARSED = "PARSED"
    VERIFYING = "VERIFYING"
    WATCH = "WATCH"
    EARLY = "EARLY"
    STRONG = "STRONG"
    CONFIRMED = "CONFIRMED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"


class Decision(StrEnum):
    NONE = "NONE"
    WATCH = "WATCH"
    EARLY = "EARLY"
    STRONG = "STRONG"
    CONFIRMED = "CONFIRMED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class DatasetSplit(StrEnum):
    IN_SAMPLE = "IN_SAMPLE"
    VALIDATION = "VALIDATION"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"


class VerificationStatus(StrEnum):
    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    NOT_FOUND = "NOT_FOUND"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class TokenCall:
    contract: str
    ticker: str
    token_name: str | None
    call_ts: datetime
    source_id: str = "default"
    source_message_id: int | None = None
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
    rule_version: str = "GEM_FILTER_V1"
    dataset_split: DatasetSplit = DatasetSplit.OUT_OF_SAMPLE

    def __post_init__(self) -> None:
        if self.call_ts.tzinfo is None:
            raise ValueError("call_ts must be timezone-aware")
        for name in (
            "top10_pct",
            "bundled_pct",
            "first50_pct",
            "jeeters_pct",
            "quickflip_pct",
            "snipers_pct",
            "insiders_pct",
            "dev_pct",
            "safe_pct",
            "poor_pct",
        ):
            value = getattr(self, name)
            if value is not None and not Decimal("0") <= value <= Decimal("100"):
                raise ValueError(f"{name} must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class WhaleEvent:
    label: str
    normalized_label: str
    event_ts: datetime
    sol_amount: Decimal | None = None
    whale_mc_usd: Decimal | None = None
    wallet_cluster: str | None = None
    signature: str | None = None
    verification_status: VerificationStatus = VerificationStatus.NOT_REQUESTED
    source_id: str | None = None
    source_message_id: int | None = None

    def distinct_key(self) -> str:
        return (self.wallet_cluster or self.normalized_label).strip().casefold()

    def event_key(self) -> tuple[str, ...]:
        """Identify one buy while preserving legitimate repeated buys by the same whale."""
        if self.source_id is not None and self.source_message_id is not None:
            return ("telegram", self.source_id, str(self.source_message_id))
        if self.signature:
            return ("signature", self.signature.strip().casefold())
        return (
            "observed",
            self.normalized_label.strip().casefold(),
            self.event_ts.isoformat(),
            str(self.sol_amount),
            str(self.whale_mc_usd),
        )


def deduplicate_whale_events(events: tuple[WhaleEvent, ...]) -> tuple[WhaleEvent, ...]:
    """Keep the latest revision for each logical Telegram message or transaction."""
    canonical: dict[tuple[str, ...], WhaleEvent] = {}
    for event in events:
        canonical[event.event_key()] = event
    return tuple(sorted(canonical.values(), key=lambda event: event.event_ts))


@dataclass(frozen=True, slots=True)
class MultipleEvent:
    multiple: int
    event_ts: datetime
    peak_mc_usd: Decimal | None = None
    elapsed_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    captured_at: datetime
    provider_ts: datetime
    provider: str
    pair_address: str | None
    dex: str | None
    market_cap_usd: Decimal | None
    fdv_usd: Decimal | None
    liquidity_usd: Decimal | None
    price_usd: Decimal | None = None
    volume_24h_usd: Decimal | None = None
    buys_5m: int | None = None
    sells_5m: int | None = None
    selection_reason: str | None = None

    def age_seconds(self, at: datetime) -> float:
        return max(0.0, (at - self.provider_ts).total_seconds())


@dataclass(frozen=True, slots=True)
class ParsedEvent:
    event_type: EventType
    parser_version: str
    payload: dict[str, Any]
    warnings: tuple[str, ...] = ()
    raw_text_hash: str = ""


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    code: str
    value: int
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HardCheck:
    code: str
    passed: bool
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScoreEvaluation:
    rule_version: str
    evaluated_at: datetime
    score: int
    decision: Decision
    hard_reject: bool
    components: tuple[ScoreComponent, ...]
    hard_checks: tuple[HardCheck, ...]
    structures: tuple[str, ...]
    input_hash: str

    @property
    def reason_codes(self) -> tuple[str, ...]:
        failed = [check.code for check in self.hard_checks if not check.passed]
        return tuple(failed + [component.code for component in self.components])


def utc_now() -> datetime:
    return datetime.now(tz=UTC)
