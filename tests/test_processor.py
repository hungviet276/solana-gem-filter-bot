from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gem_filter.application.live_evaluation import evaluate_live
from gem_filter.domain.models import Decision, MarketSnapshot, TokenCall, WhaleEvent
from gem_filter.domain.rules import DEFAULT_RULE, evaluate


def test_live_evaluation_keeps_snapshot_captured_after_request_started() -> None:
    snapshot_time = datetime.now(tz=UTC)
    request_started_at = snapshot_time - timedelta(seconds=1)
    token = TokenCall(
        contract="A" * 44,
        ticker="GEM",
        token_name="Gem",
        call_ts=snapshot_time - timedelta(minutes=2),
        call_mc_usd=Decimal("50000"),
        age_seconds=120,
        holders=383,
        top10_pct=Decimal("23"),
        bundled_pct=Decimal("10"),
        first50_pct=Decimal("9.3"),
        jeeters_pct=Decimal("8.6"),
        quickflip_pct=Decimal("0"),
        snipers_pct=Decimal("3"),
        insiders_pct=Decimal("0"),
        dev_pct=Decimal("0"),
        safe_pct=Decimal("43"),
        poor_pct=Decimal("5"),
    )
    market = MarketSnapshot(
        captured_at=snapshot_time,
        provider_ts=snapshot_time,
        provider="test",
        pair_address="pair",
        dex="test",
        market_cap_usd=Decimal("50000"),
        fdv_usd=Decimal("50000"),
        liquidity_usd=Decimal("10000"),
    )
    whales = (
        WhaleEvent(
            label="FEBU",
            normalized_label="febu",
            event_ts=snapshot_time - timedelta(minutes=1),
            sol_amount=Decimal("4.37"),
            whale_mc_usd=Decimal("50000"),
        ),
    )

    old_result = evaluate(
        token,
        market,
        whales,
        (),
        DEFAULT_RULE,
        evaluation_time=request_started_at,
    )
    live_result = evaluate_live(token, market, whales, (), DEFAULT_RULE)

    assert old_result.decision is Decision.REJECTED
    assert "MARKET_STALE" in old_result.reason_codes
    assert live_result.decision is Decision.STRONG
    assert "MARKET_STALE" not in live_result.reason_codes
