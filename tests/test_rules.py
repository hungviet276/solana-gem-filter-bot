from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gem_filter.domain.models import Decision, MarketSnapshot, MultipleEvent, TokenCall, WhaleEvent
from gem_filter.domain.rules import DEFAULT_RULE, RuleConfig, evaluate

BASE = datetime(2026, 7, 8, 2, 23, 48, tzinfo=UTC)
CONTRACT = "A" * 44


def token(**overrides) -> TokenCall:
    values = {
        "contract": CONTRACT,
        "ticker": "GEM",
        "token_name": "Gem",
        "call_ts": BASE,
        "call_mc_usd": Decimal("40000"),
        "age_seconds": 300,
        "holders": 150,
        "top10_pct": Decimal("18"),
        "bundled_pct": Decimal("3"),
        "first50_pct": Decimal("55"),
        "jeeters_pct": Decimal("4"),
        "quickflip_pct": Decimal("2"),
        "snipers_pct": Decimal("2"),
        "insiders_pct": Decimal("2"),
        "dev_pct": Decimal("0"),
        "safe_pct": Decimal("40"),
        "poor_pct": Decimal("1"),
    }
    values.update(overrides)
    return TokenCall(**values)


def market(at: datetime, mc: str = "40000", liquidity: str = "20000") -> MarketSnapshot:
    return MarketSnapshot(
        captured_at=at,
        provider_ts=at,
        provider="test",
        pair_address="pair",
        dex="raydium",
        market_cap_usd=Decimal(mc),
        fdv_usd=Decimal(mc),
        liquidity_usd=Decimal(liquidity),
    )


def whale(label: str, minutes: int, sol: str = "6") -> WhaleEvent:
    return WhaleEvent(
        label=label,
        normalized_label=label.casefold(),
        event_ts=BASE + timedelta(minutes=minutes),
        sol_amount=Decimal(sol),
        whale_mc_usd=Decimal("45000"),
    )


class RuleTests(unittest.TestCase):
    def test_tc001_genesis_without_whale_is_watch(self) -> None:
        result = evaluate(token(), market(BASE), (), (), evaluation_time=BASE)
        self.assertEqual(result.score, 30)
        self.assertEqual(result.decision, Decision.WATCH)
        self.assertIn("GENESIS", result.structures)

    def test_tc002_first_whale_in_three_minutes_scores_early_signal(self) -> None:
        at = BASE + timedelta(minutes=3)
        result = evaluate(token(), market(at), (whale("OPAL", 3),), (), evaluation_time=at)
        self.assertEqual(result.score, 60)
        self.assertEqual(result.decision, Decision.STRONG)
        self.assertIn("WHALE_1_5M", result.reason_codes)

    def test_tc003_repeat_same_whale_does_not_add_distinct_score(self) -> None:
        at = BASE + timedelta(minutes=4)
        result = evaluate(
            token(), market(at), (whale("OPAL", 3), whale("opal", 4)), (), evaluation_time=at
        )
        codes = [component.code for component in result.components]
        self.assertEqual(codes.count("WHALE_1_5M"), 1)
        self.assertNotIn("WHALE_2_15M", codes)

    def test_tc004_second_distinct_whale_in_twelve_minutes(self) -> None:
        at = BASE + timedelta(minutes=12)
        result = evaluate(
            token(), market(at), (whale("OPAL", 3), whale("ANSEM", 12)), (), evaluation_time=at
        )
        self.assertIn("WHALE_2_15M", result.reason_codes)
        self.assertIn("EARLY_WHALE_SOL_10", result.reason_codes)
        self.assertEqual(result.decision, Decision.STRONG)

    def test_tc005_whale_after_x2_does_not_score_early(self) -> None:
        at = BASE + timedelta(minutes=4)
        outcomes = (MultipleEvent(multiple=2, event_ts=BASE + timedelta(minutes=2)),)
        result = evaluate(token(), market(at), (whale("OPAL", 3),), outcomes, evaluation_time=at)
        self.assertNotIn("WHALE_1_5M", result.reason_codes)

    def test_tc006_hard_anti_chase_blocks_entry(self) -> None:
        at = BASE + timedelta(minutes=3)
        result = evaluate(
            token(), market(at, mc="84000"), (whale("OPAL", 3),), (), evaluation_time=at
        )
        self.assertTrue(result.hard_reject)
        self.assertEqual(result.decision, Decision.REJECTED)
        self.assertIn("ANTI_CHASE_HARD", result.reason_codes)

    def test_tc008_stale_provider_blocks_strong(self) -> None:
        at = BASE + timedelta(seconds=45)
        result = evaluate(token(), market(BASE), (), (), evaluation_time=at)
        self.assertEqual(result.decision, Decision.REJECTED)
        self.assertIn("MARKET_STALE", result.reason_codes)

    def test_low_liquidity_is_hard_reject(self) -> None:
        result = evaluate(token(), market(BASE, liquidity="4999"), (), (), evaluation_time=BASE)
        self.assertIn("LIQUIDITY_LOW", result.reason_codes)
        self.assertTrue(result.hard_reject)

    def test_future_whale_and_outcome_are_invisible(self) -> None:
        result = evaluate(
            token(),
            market(BASE),
            (whale("FUTURE", 3),),
            (MultipleEvent(multiple=10, event_ts=BASE + timedelta(minutes=5)),),
            evaluation_time=BASE,
        )
        self.assertNotIn("WHALE_1_5M", result.reason_codes)
        self.assertEqual(result.decision, Decision.WATCH)

    def test_input_hash_is_deterministic(self) -> None:
        first = evaluate(token(), market(BASE), (), (), DEFAULT_RULE, evaluation_time=BASE)
        second = evaluate(token(), market(BASE), (), (), DEFAULT_RULE, evaluation_time=BASE)
        self.assertEqual(first.input_hash, second.input_hash)

    def test_null_metrics_are_not_treated_as_zero(self) -> None:
        sparse = token(age_seconds=None, holders=None, first50_pct=None, safe_pct=None)
        result = evaluate(sparse, market(BASE), (), (), evaluation_time=BASE)
        self.assertNotIn("GENESIS", result.reason_codes)
        self.assertNotIn("SAFE_LOW", result.reason_codes)
        self.assertTrue(result.hard_reject)
        self.assertIn("SOURCE_DATA_INCOMPLETE", result.reason_codes)

    def test_missing_dev_and_call_mc_fail_closed(self) -> None:
        result = evaluate(
            token(dev_pct=None, call_mc_usd=None), market(BASE), (), (), evaluation_time=BASE
        )
        self.assertTrue(result.hard_reject)
        self.assertIn("SOURCE_DATA_INCOMPLETE", result.reason_codes)
        self.assertIn("DEV_HIGH", result.reason_codes)
        self.assertIn("ANTI_CHASE_HARD", result.reason_codes)

    def test_dev_limit_is_hard_reject(self) -> None:
        result = evaluate(token(dev_pct=Decimal("10")), market(BASE), (), (), evaluation_time=BASE)
        self.assertEqual(result.decision, Decision.REJECTED)
        self.assertIn("DEV_HIGH", result.reason_codes)

    def test_traction_and_soft_risk_components(self) -> None:
        traction = token(
            age_seconds=1800,
            holders=400,
            first50_pct=Decimal("20"),
            top10_pct=Decimal("22"),
            safe_pct=Decimal("10"),
            poor_pct=Decimal("15"),
            snipers_pct=Decimal("15"),
            bundled_pct=Decimal("25"),
        )
        result = evaluate(traction, market(BASE), (), (), evaluation_time=BASE)
        self.assertIn("TRACTION", result.structures)
        self.assertIn("TOP10_BALANCED", result.reason_codes)
        self.assertIn("SAFE_LOW", result.reason_codes)
        self.assertIn("POOR_HIGH_SOFT", result.reason_codes)
        self.assertIn("SNIPERS_HIGH", result.reason_codes)
        self.assertIn("BUNDLED_HIGH", result.reason_codes)

    def test_third_whale_scores_once(self) -> None:
        at = BASE + timedelta(minutes=14)
        whales = (
            whale("ONE", 2, "4"),
            whale("TWO", 10, "4"),
            whale("THREE", 14, "4"),
        )
        result = evaluate(token(), market(at), whales, (), evaluation_time=at)
        self.assertIn("WHALE_3", result.reason_codes)

    def test_invalid_contract_is_rejected(self) -> None:
        result = evaluate(token(contract="not-base58"), market(BASE), (), (), evaluation_time=BASE)
        self.assertIn("INVALID_CONTRACT", result.reason_codes)

    def test_market_discrepancy_is_explained(self) -> None:
        result = evaluate(token(), market(BASE, mc="60000"), (), (), evaluation_time=BASE)
        self.assertIn("MC_DISCREPANCY_WARNING", result.reason_codes)

    def test_rule_configuration_validation(self) -> None:
        with self.assertRaises(ValueError):
            RuleConfig(watch_threshold=60, strong_threshold=55)
        with self.assertRaises(ValueError):
            RuleConfig(soft_chase_ratio=Decimal("2.1"), hard_chase_ratio=Decimal("2"))
        with self.assertRaises(ValueError):
            RuleConfig(max_snapshot_age_seconds=0)
        with self.assertRaises(ValueError):
            RuleConfig(strategy="UNKNOWN")
        with self.assertRaises(ValueError):
            RuleConfig(min_whale_flow_sol=Decimal("0"))
        with self.assertRaises(ValueError):
            RuleConfig(quality_top10_min_pct=Decimal("30"), quality_top10_max_pct=Decimal("20"))


if __name__ == "__main__":
    unittest.main()
