from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gem_filter.domain.alerts import render_alert
from gem_filter.domain.models import Decision, MarketSnapshot, MultipleEvent, TokenCall, WhaleEvent
from gem_filter.domain.rules import FLOW_8_RULE, evaluate

BASE = datetime(2026, 7, 19, 5, 39, 7, tzinfo=UTC)
CONTRACT = "A" * 44


def token(**overrides) -> TokenCall:
    values = {
        "contract": CONTRACT,
        "ticker": "HOME",
        "token_name": "Homecoin",
        "call_ts": BASE,
        "call_mc_usd": Decimal("45200"),
        "age_seconds": 480,
        "holders": 399,
        "top10_pct": Decimal("23"),
        "bundled_pct": Decimal("6"),
        "first50_pct": Decimal("6.2"),
        "jeeters_pct": Decimal("15"),
        "quickflip_pct": Decimal("0"),
        "snipers_pct": Decimal("0"),
        "insiders_pct": Decimal("0"),
        "dev_pct": Decimal("0"),
        "safe_pct": Decimal("40"),
        "poor_pct": Decimal("9"),
        "rule_version": FLOW_8_RULE.version,
    }
    values.update(overrides)
    return TokenCall(**values)


def market(at: datetime, mc: str = "63300") -> MarketSnapshot:
    return MarketSnapshot(
        captured_at=at,
        provider_ts=at,
        provider="test",
        pair_address="pair",
        dex="raydium",
        market_cap_usd=Decimal(mc),
        fdv_usd=Decimal(mc),
        liquidity_usd=Decimal("10000"),
    )


def whale(label: str, seconds: int, sol: str) -> WhaleEvent:
    return WhaleEvent(
        label=label,
        normalized_label=label.casefold(),
        event_ts=BASE + timedelta(seconds=seconds),
        sol_amount=Decimal(sol),
        whale_mc_usd=Decimal("63300"),
    )


class FlowGateRuleTests(unittest.TestCase):
    def test_home_triggers_strong_on_first_8_sol_whale(self) -> None:
        at = BASE + timedelta(seconds=177)
        result = evaluate(
            token(),
            market(at),
            (whale("JOTCHUA", 177, "8.47"),),
            (),
            FLOW_8_RULE,
            evaluation_time=at,
        )

        self.assertEqual(result.score, 60)
        self.assertEqual(result.decision, Decision.STRONG)
        self.assertIn("TOP10_QUALITY", result.structures)
        self.assertIn("V2_QUALITY_STRUCTURE", result.reason_codes)
        self.assertIn("V2_WHALE_FLOW_GATE", result.reason_codes)
        self.assertNotIn("V2_MULTI_WHALE_CONFIRMED", result.reason_codes)
        rendered = render_alert(
            token(),
            market(at),
            (whale("JOTCHUA", 177, "8.47"),),
            result,
        )
        self.assertIn("Total flow: 8.47 SOL/15m | Distinct: 1", rendered)

    def test_small_whales_remain_watch_for_known_false_positives(self) -> None:
        cases = (
            ("CHARLOTTE", "4.37"),
            ("SPY", "3.17"),
            ("BTC", "3.28"),
        )
        for ticker, sol_amount in cases:
            with self.subTest(ticker=ticker):
                at = BASE + timedelta(seconds=180)
                result = evaluate(
                    token(ticker=ticker),
                    market(at),
                    (whale("TRACKED", 180, sol_amount),),
                    (),
                    FLOW_8_RULE,
                    evaluation_time=at,
                )
                self.assertEqual(result.decision, Decision.WATCH)
                self.assertIn("V2_WHALE_FLOW_PENDING", result.reason_codes)
                self.assertNotIn("V2_WHALE_FLOW_GATE", result.reason_codes)

    def test_large_whale_without_quality_structure_remains_watch(self) -> None:
        at = BASE + timedelta(seconds=300)
        geminauts = token(
            ticker="GEMINAUTS",
            age_seconds=300,
            holders=542,
            top10_pct=Decimal("14"),
            first50_pct=Decimal("10"),
        )
        result = evaluate(
            geminauts,
            market(at),
            (whale("THREE", 300, "14.4"),),
            (),
            FLOW_8_RULE,
            evaluation_time=at,
        )

        self.assertEqual(result.decision, Decision.WATCH)
        self.assertIn("V2_WHALE_FLOW_GATE", result.reason_codes)
        self.assertNotIn("V2_QUALITY_STRUCTURE", result.reason_codes)

    def test_genesis_is_an_alternative_quality_structure(self) -> None:
        at = BASE + timedelta(seconds=120)
        genesis = token(
            ticker="GENESIS",
            age_seconds=300,
            holders=150,
            top10_pct=Decimal("14"),
            first50_pct=Decimal("55"),
        )
        result = evaluate(
            genesis,
            market(at),
            (whale("OPAL", 120, "8"),),
            (),
            FLOW_8_RULE,
            evaluation_time=at,
        )

        self.assertEqual(result.decision, Decision.STRONG)
        self.assertIn("GENESIS", result.structures)

    def test_repeated_buys_count_toward_flow_but_not_distinct_confirmation(self) -> None:
        at = BASE + timedelta(seconds=240)
        result = evaluate(
            token(),
            market(at),
            (
                whale("OPAL", 120, "4"),
                whale("opal", 240, "4.5"),
            ),
            (),
            FLOW_8_RULE,
            evaluation_time=at,
        )

        self.assertEqual(result.decision, Decision.STRONG)
        self.assertIn("V2_WHALE_FLOW_GATE", result.reason_codes)
        self.assertNotIn("V2_MULTI_WHALE_CONFIRMED", result.reason_codes)

    def test_two_distinct_whales_add_confirmation(self) -> None:
        at = BASE + timedelta(seconds=300)
        result = evaluate(
            token(),
            market(at),
            (
                whale("OPAL", 120, "4"),
                whale("ANSEM", 300, "4.5"),
            ),
            (),
            FLOW_8_RULE,
            evaluation_time=at,
        )

        self.assertEqual(result.score, 70)
        self.assertEqual(result.decision, Decision.STRONG)
        self.assertIn("V2_MULTI_WHALE_CONFIRMED", result.reason_codes)

    def test_whales_after_window_or_x2_do_not_open_gate(self) -> None:
        after_window = BASE + timedelta(seconds=901)
        late_result = evaluate(
            token(),
            market(after_window),
            (whale("LATE", 901, "20"),),
            (),
            FLOW_8_RULE,
            evaluation_time=after_window,
        )
        self.assertEqual(late_result.decision, Decision.WATCH)
        self.assertNotIn("V2_WHALE_FLOW_GATE", late_result.reason_codes)

        after_x2 = BASE + timedelta(seconds=180)
        x2_result = evaluate(
            token(),
            market(after_x2),
            (whale("LATE", 180, "20"),),
            (MultipleEvent(multiple=2, event_ts=BASE + timedelta(seconds=120)),),
            FLOW_8_RULE,
            evaluation_time=after_x2,
        )
        self.assertEqual(x2_result.decision, Decision.WATCH)
        self.assertNotIn("V2_WHALE_FLOW_GATE", x2_result.reason_codes)

    def test_hard_chase_still_rejects_flow_signal(self) -> None:
        at = BASE + timedelta(seconds=177)
        result = evaluate(
            token(),
            market(at, mc="91000"),
            (whale("JOTCHUA", 177, "8.47"),),
            (),
            FLOW_8_RULE,
            evaluation_time=at,
        )

        self.assertTrue(result.hard_reject)
        self.assertEqual(result.decision, Decision.REJECTED)
        self.assertIn("ANTI_CHASE_HARD", result.reason_codes)


if __name__ == "__main__":
    unittest.main()
