from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from .models import (
    Decision,
    HardCheck,
    MarketSnapshot,
    MultipleEvent,
    ScoreComponent,
    ScoreEvaluation,
    TokenCall,
    WhaleEvent,
    deduplicate_whale_events,
)

SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,64}$")


@dataclass(frozen=True, slots=True)
class RuleConfig:
    version: str = "GEM_FILTER_V1"
    strategy: str = "V1_SCORE"
    max_snapshot_age_seconds: int = 20
    expiry_seconds: int = 3600
    mc_discrepancy_warn_pct: Decimal = Decimal("30")
    min_liquidity_usd: Decimal | None = Decimal("5000")
    dev_hard_limit_pct: Decimal = Decimal("10")
    poor_hard_limit_pct: Decimal | None = None
    soft_chase_ratio: Decimal = Decimal("1.5")
    hard_chase_ratio: Decimal = Decimal("2.0")
    watch_threshold: int = 15
    early_threshold: int = 40
    strong_threshold: int = 55
    whale_window_seconds: int = 900
    min_whale_flow_sol: Decimal = Decimal("8")
    quality_top10_min_pct: Decimal = Decimal("20")
    quality_top10_max_pct: Decimal = Decimal("25")
    genesis_max_age_seconds: int = 600
    genesis_max_holders: int = 200
    genesis_first50_min_pct: Decimal = Decimal("50")
    confirmation_distinct_whales: int = 2

    def __post_init__(self) -> None:
        if self.strategy not in {"V1_SCORE", "FLOW_GATE_V2"}:
            raise ValueError("unsupported rule strategy")
        if self.max_snapshot_age_seconds <= 0 or self.expiry_seconds <= 0:
            raise ValueError("snapshot age and expiry must be positive")
        if self.whale_window_seconds <= 0:
            raise ValueError("whale window must be positive")
        if self.min_whale_flow_sol <= 0:
            raise ValueError("minimum whale flow must be positive")
        if self.confirmation_distinct_whales < 2:
            raise ValueError("confirmation requires at least two distinct whales")
        if not 0 <= self.watch_threshold <= self.early_threshold <= self.strong_threshold <= 100:
            raise ValueError("decision thresholds must be ordered within 0..100")
        if self.soft_chase_ratio >= self.hard_chase_ratio:
            raise ValueError("soft chase ratio must be lower than hard chase ratio")
        if not Decimal("0") <= self.dev_hard_limit_pct <= Decimal("100"):
            raise ValueError("dev hard limit must be within 0..100")
        if not (
            Decimal("0")
            <= self.quality_top10_min_pct
            <= self.quality_top10_max_pct
            <= Decimal("100")
        ):
            raise ValueError("quality Top10 range must be ordered within 0..100")
        if self.genesis_max_age_seconds <= 0 or self.genesis_max_holders <= 0:
            raise ValueError("genesis limits must be positive")
        if not Decimal("0") <= self.genesis_first50_min_pct <= Decimal("100"):
            raise ValueError("genesis First50 minimum must be within 0..100")


V1_RULE = RuleConfig()
FLOW_8_RULE = RuleConfig(
    version="GEM_FILTER_V2_FLOW_8",
    strategy="FLOW_GATE_V2",
)
DEFAULT_RULE = V1_RULE
BUILTIN_RULES = {
    V1_RULE.version: V1_RULE,
    FLOW_8_RULE.version: FLOW_8_RULE,
}


def builtin_rule(version: str) -> RuleConfig:
    rule = BUILTIN_RULES.get(version)
    return rule if rule is not None else RuleConfig(version=version)


REQUIRED_SOURCE_METRICS = (
    "call_mc_usd",
    "age_seconds",
    "holders",
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
)


def _decimal(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _input_hash(
    token: TokenCall,
    market: MarketSnapshot | None,
    whales: Iterable[WhaleEvent],
    outcomes: Iterable[MultipleEvent],
    rule: RuleConfig,
    evaluation_time: datetime,
) -> str:
    payload = {
        "token": {key: str(value) for key, value in asdict(token).items()},
        "market": {key: str(value) for key, value in asdict(market).items()} if market else None,
        "whales": [
            {key: str(value) for key, value in asdict(item).items()}
            for item in sorted(whales, key=lambda event: event.event_ts)
        ],
        "outcomes": [
            {key: str(value) for key, value in asdict(item).items()}
            for item in sorted(outcomes, key=lambda event: event.event_ts)
        ],
        "rule": {key: str(value) for key, value in asdict(rule).items()},
        "evaluation_time": evaluation_time.isoformat(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _between(value: Decimal | int | None, low: Decimal | int, high: Decimal | int) -> bool:
    return value is not None and low <= value <= high


def _decision(score: int, rule: RuleConfig) -> Decision:
    if score >= rule.strong_threshold:
        return Decision.STRONG
    if score >= rule.early_threshold:
        return Decision.EARLY
    if score >= rule.watch_threshold:
        return Decision.WATCH
    return Decision.NONE


def evaluate(
    token: TokenCall,
    market: MarketSnapshot | None,
    whales: Iterable[WhaleEvent],
    outcomes: Iterable[MultipleEvent],
    rule: RuleConfig = DEFAULT_RULE,
    *,
    evaluation_time: datetime,
) -> ScoreEvaluation:
    """Evaluate only information available at ``evaluation_time``.

    Future whale, result and market data is deliberately discarded. This invariant is shared by
    live processing, simulation and backtest to prevent label leakage.
    """
    if evaluation_time.tzinfo is None:
        raise ValueError("evaluation_time must be timezone-aware")

    visible_whales = deduplicate_whale_events(
        tuple(
            sorted((w for w in whales if w.event_ts <= evaluation_time), key=lambda w: w.event_ts)
        )
    )
    visible_outcomes = tuple(
        sorted((o for o in outcomes if o.event_ts <= evaluation_time), key=lambda o: o.event_ts)
    )
    visible_market = (
        market if market is not None and market.provider_ts <= evaluation_time else None
    )
    checks: list[HardCheck] = []
    components: list[ScoreComponent] = []
    structures: list[str] = []

    valid_contract = bool(SOLANA_ADDRESS_RE.fullmatch(token.contract))
    checks.append(HardCheck("INVALID_CONTRACT", valid_contract, {"contract": token.contract}))

    missing_source_metrics = [
        field for field in REQUIRED_SOURCE_METRICS if getattr(token, field) is None
    ]
    checks.append(
        HardCheck(
            "SOURCE_DATA_INCOMPLETE",
            not missing_source_metrics,
            {"missing_fields": missing_source_metrics},
        )
    )

    market_fresh = (
        visible_market is not None
        and visible_market.age_seconds(evaluation_time) <= rule.max_snapshot_age_seconds
    )
    checks.append(
        HardCheck(
            "MARKET_STALE",
            market_fresh,
            {
                "max_age_seconds": rule.max_snapshot_age_seconds,
                "actual_age_seconds": visible_market.age_seconds(evaluation_time)
                if visible_market
                else None,
            },
        )
    )

    verified_mc = None
    if visible_market:
        verified_mc = visible_market.market_cap_usd or visible_market.fdv_usd
    market_valid = verified_mc is not None and verified_mc > 0
    checks.append(
        HardCheck(
            "MARKET_INVALID",
            market_valid,
            {"market_cap_usd": _decimal(verified_mc)},
        )
    )

    dev_ok = token.dev_pct is not None and token.dev_pct < rule.dev_hard_limit_pct
    checks.append(
        HardCheck(
            "DEV_HIGH",
            dev_ok,
            {"value": _decimal(token.dev_pct), "limit": _decimal(rule.dev_hard_limit_pct)},
        )
    )

    poor_ok = (
        rule.poor_hard_limit_pct is None
        or token.poor_pct is None
        or token.poor_pct < rule.poor_hard_limit_pct
    )
    checks.append(
        HardCheck(
            "POOR_HIGH",
            poor_ok,
            {"value": _decimal(token.poor_pct), "limit": _decimal(rule.poor_hard_limit_pct)},
        )
    )

    liquidity_ok = rule.min_liquidity_usd is None or (
        visible_market is not None
        and visible_market.liquidity_usd is not None
        and visible_market.liquidity_usd >= rule.min_liquidity_usd
    )
    checks.append(
        HardCheck(
            "LIQUIDITY_LOW",
            liquidity_ok,
            {
                "value": _decimal(visible_market.liquidity_usd) if visible_market else None,
                "limit": _decimal(rule.min_liquidity_usd),
            },
        )
    )

    ratio: Decimal | None = None
    if verified_mc and token.call_mc_usd:
        ratio = verified_mc / token.call_mc_usd
    chase_ok = ratio is not None and ratio <= rule.hard_chase_ratio
    checks.append(
        HardCheck(
            "ANTI_CHASE_HARD",
            chase_ok,
            {"ratio": _decimal(ratio), "limit": _decimal(rule.hard_chase_ratio)},
        )
    )

    hard_reject = any(not check.passed for check in checks)
    is_flow_gate = rule.strategy == "FLOW_GATE_V2"

    genesis = (
        token.age_seconds is not None
        and token.age_seconds <= rule.genesis_max_age_seconds
        and token.holders is not None
        and token.holders <= rule.genesis_max_holders
        and token.first50_pct is not None
        and token.first50_pct >= rule.genesis_first50_min_pct
    )
    if genesis:
        structures.append("GENESIS")
        if not is_flow_gate:
            components.append(
                ScoreComponent(
                    "GENESIS",
                    25,
                    {
                        "age_seconds": token.age_seconds,
                        "holders": token.holders,
                        "first50_pct": _decimal(token.first50_pct),
                    },
                )
            )

    traction = (
        token.age_seconds is not None
        and token.age_seconds <= 3600
        and token.holders is not None
        and 300 <= token.holders <= 500
        and _between(token.top10_pct, Decimal("17"), Decimal("25"))
    )
    if traction:
        structures.append("TRACTION")
        if not is_flow_gate:
            components.append(
                ScoreComponent(
                    "TRACTION",
                    15,
                    {
                        "age_seconds": token.age_seconds,
                        "holders": token.holders,
                        "top10_pct": _decimal(token.top10_pct),
                    },
                )
            )

    top10_quality = _between(
        token.top10_pct,
        rule.quality_top10_min_pct,
        rule.quality_top10_max_pct,
    )
    if top10_quality and not is_flow_gate:
        components.append(
            ScoreComponent("TOP10_BALANCED", 5, {"top10_pct": _decimal(token.top10_pct)})
        )
    if not is_flow_gate and token.safe_pct is not None and token.safe_pct >= Decimal("35"):
        components.append(ScoreComponent("SAFE_35", 5, {"safe_pct": _decimal(token.safe_pct)}))

    if verified_mc and token.call_mc_usd:
        discrepancy = abs(verified_mc - token.call_mc_usd) / token.call_mc_usd * Decimal("100")
        if discrepancy > rule.mc_discrepancy_warn_pct:
            components.append(
                ScoreComponent(
                    "MC_DISCREPANCY_WARNING",
                    0,
                    {
                        "discrepancy_pct": _decimal(discrepancy),
                        "warn_pct": _decimal(rule.mc_discrepancy_warn_pct),
                    },
                )
            )

    x2_times = [outcome.event_ts for outcome in visible_outcomes if outcome.multiple >= 2]
    x2_ts = min(x2_times) if x2_times else None
    early_whales: list[WhaleEvent] = []
    early_by_key: dict[str, WhaleEvent] = {}
    for whale in visible_whales:
        elapsed = (whale.event_ts - token.call_ts).total_seconds()
        before_x2 = x2_ts is None or whale.event_ts < x2_ts
        if elapsed < 0 or elapsed > rule.whale_window_seconds or not before_x2:
            continue
        early_whales.append(whale)
        early_by_key.setdefault(whale.distinct_key(), whale)
    distinct = tuple(sorted(early_by_key.values(), key=lambda whale: whale.event_ts))

    if is_flow_gate:
        quality_structure = genesis or top10_quality
        if top10_quality and "TOP10_QUALITY" not in structures:
            structures.append("TOP10_QUALITY")
        if quality_structure:
            components.append(
                ScoreComponent(
                    "V2_QUALITY_STRUCTURE",
                    25,
                    {
                        "genesis": genesis,
                        "top10_quality": top10_quality,
                        "top10_pct": _decimal(token.top10_pct),
                    },
                )
            )
        whale_flow_sol = sum(
            (whale.sol_amount or Decimal("0") for whale in early_whales),
            Decimal("0"),
        )
        whale_flow_ready = whale_flow_sol >= rule.min_whale_flow_sol
        if whale_flow_ready:
            components.append(
                ScoreComponent(
                    "V2_WHALE_FLOW_GATE",
                    35,
                    {
                        "sol_amount": _decimal(whale_flow_sol),
                        "minimum_sol": _decimal(rule.min_whale_flow_sol),
                        "window_seconds": rule.whale_window_seconds,
                        "distinct_count": len(distinct),
                    },
                )
            )
        elif early_whales:
            components.append(
                ScoreComponent(
                    "V2_WHALE_FLOW_PENDING",
                    0,
                    {
                        "sol_amount": _decimal(whale_flow_sol),
                        "minimum_sol": _decimal(rule.min_whale_flow_sol),
                        "window_seconds": rule.whale_window_seconds,
                    },
                )
            )
        if (
            quality_structure
            and whale_flow_ready
            and len(distinct) >= rule.confirmation_distinct_whales
        ):
            components.append(
                ScoreComponent(
                    "V2_MULTI_WHALE_CONFIRMED",
                    10,
                    {
                        "distinct_count": len(distinct),
                        "minimum_count": rule.confirmation_distinct_whales,
                    },
                )
            )
        if ratio is not None and ratio > rule.soft_chase_ratio:
            components.append(
                ScoreComponent(
                    "V2_CHASE_WARNING_1_5X",
                    0,
                    {"ratio": _decimal(ratio)},
                )
            )

        raw_score = sum(component.value for component in components)
        score = max(0, min(100, raw_score))
        if hard_reject:
            decision = Decision.REJECTED
        elif quality_structure and whale_flow_ready:
            decision = Decision.STRONG
        elif quality_structure or early_whales:
            decision = Decision.WATCH
        else:
            decision = Decision.NONE
        return ScoreEvaluation(
            rule_version=rule.version,
            evaluated_at=evaluation_time,
            score=score,
            decision=decision,
            hard_reject=hard_reject,
            components=tuple(components),
            hard_checks=tuple(checks),
            structures=tuple(structures),
            input_hash=_input_hash(
                token, visible_market, visible_whales, visible_outcomes, rule, evaluation_time
            ),
        )

    if distinct:
        elapsed = int((distinct[0].event_ts - token.call_ts).total_seconds())
        if elapsed <= 300:
            components.append(
                ScoreComponent(
                    "WHALE_1_5M",
                    30,
                    {"label": distinct[0].normalized_label, "elapsed_seconds": elapsed},
                )
            )
    if len(distinct) >= 2:
        elapsed = int((distinct[1].event_ts - token.call_ts).total_seconds())
        components.append(
            ScoreComponent(
                "WHALE_2_15M",
                15,
                {"label": distinct[1].normalized_label, "elapsed_seconds": elapsed},
            )
        )
    early_sol = sum((whale.sol_amount or Decimal("0") for whale in distinct), Decimal("0"))
    if early_sol >= Decimal("10"):
        components.append(
            ScoreComponent("EARLY_WHALE_SOL_10", 10, {"sol_amount": _decimal(early_sol)})
        )
    if len(distinct) >= 3:
        components.append(ScoreComponent("WHALE_3", 5, {"distinct_count": len(distinct)}))

    if ratio is not None and ratio > rule.soft_chase_ratio:
        components.append(ScoreComponent("ANTI_CHASE_1_5X", -10, {"ratio": _decimal(ratio)}))
    if token.safe_pct is not None and token.safe_pct < Decimal("15"):
        components.append(ScoreComponent("SAFE_LOW", -15, {"safe_pct": _decimal(token.safe_pct)}))
    if token.poor_pct is not None and token.poor_pct >= Decimal("15"):
        components.append(
            ScoreComponent("POOR_HIGH_SOFT", -15, {"poor_pct": _decimal(token.poor_pct)})
        )
    if token.snipers_pct is not None and token.snipers_pct >= Decimal("15"):
        components.append(
            ScoreComponent("SNIPERS_HIGH", -10, {"snipers_pct": _decimal(token.snipers_pct)})
        )
    if token.bundled_pct is not None and token.bundled_pct >= Decimal("25"):
        components.append(
            ScoreComponent("BUNDLED_HIGH", -10, {"bundled_pct": _decimal(token.bundled_pct)})
        )
    if token.dev_pct is not None and token.dev_pct >= rule.dev_hard_limit_pct:
        components.append(
            ScoreComponent("DEV_HIGH_SCORE", -20, {"dev_pct": _decimal(token.dev_pct)})
        )

    raw_score = sum(component.value for component in components)
    score = max(0, min(100, raw_score))
    decision = Decision.REJECTED if hard_reject else _decision(score, rule)
    return ScoreEvaluation(
        rule_version=rule.version,
        evaluated_at=evaluation_time,
        score=score,
        decision=decision,
        hard_reject=hard_reject,
        components=tuple(components),
        hard_checks=tuple(checks),
        structures=tuple(structures),
        input_hash=_input_hash(
            token, visible_market, visible_whales, visible_outcomes, rule, evaluation_time
        ),
    )
