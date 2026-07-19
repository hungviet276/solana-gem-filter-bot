from __future__ import annotations

from datetime import UTC, datetime

from gem_filter.domain.models import (
    MarketSnapshot,
    MultipleEvent,
    ScoreEvaluation,
    TokenCall,
    WhaleEvent,
)
from gem_filter.domain.rules import RuleConfig, evaluate


def evaluate_live(
    token: TokenCall,
    market: MarketSnapshot | None,
    whales: tuple[WhaleEvent, ...],
    outcomes: tuple[MultipleEvent, ...],
    rule: RuleConfig,
) -> ScoreEvaluation:
    """Evaluate after live inputs have been captured so a fresh snapshot stays visible."""
    return evaluate(
        token,
        market,
        whales,
        outcomes,
        rule,
        evaluation_time=datetime.now(tz=UTC),
    )
