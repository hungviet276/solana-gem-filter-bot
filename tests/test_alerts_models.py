from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gem_filter.domain.alerts import DISCLAIMER, _money, render_alert
from gem_filter.domain.models import MarketSnapshot, TokenCall, WhaleEvent
from gem_filter.domain.rules import evaluate


class AlertAndModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.at = datetime(2026, 7, 8, tzinfo=UTC)
        self.token = TokenCall(
            contract="A" * 44,
            ticker="GEM",
            token_name="Gem",
            call_ts=self.at,
            call_mc_usd=Decimal("40000"),
            age_seconds=60,
            holders=100,
            first50_pct=Decimal("60"),
            safe_pct=Decimal("40"),
            dev_pct=Decimal("0"),
        )

    def snapshot(self, mc: str = "50000") -> MarketSnapshot:
        return MarketSnapshot(
            self.at,
            self.at,
            "test",
            "pair",
            "raydium",
            Decimal(mc),
            Decimal(mc),
            Decimal("10000"),
        )

    def test_render_alert_includes_explainability_and_disclaimer(self) -> None:
        whale = WhaleEvent(
            "Opal",
            "opal",
            self.at + timedelta(minutes=2),
            Decimal("12"),
            Decimal("45000"),
        )
        evaluation = evaluate(
            self.token,
            MarketSnapshot(
                whale.event_ts,
                whale.event_ts,
                "test",
                "pair",
                "raydium",
                Decimal("50000"),
                Decimal("50000"),
                Decimal("10000"),
            ),
            (whale,),
            (),
            evaluation_time=whale.event_ts,
        )
        rendered = render_alert(self.token, self.snapshot(), (whale,), evaluation)
        self.assertIn("STRONG", rendered)
        self.assertIn("WHALE CONVERGENCE", rendered)
        self.assertIn("WHALE_1_5M", rendered)
        self.assertIn(DISCLAIMER, rendered)

    def test_render_rejected_alert_and_money_units(self) -> None:
        evaluation = evaluate(
            self.token,
            self.snapshot("90000"),
            (),
            (),
            evaluation_time=self.at,
        )
        rendered = render_alert(self.token, self.snapshot("90000"), (), evaluation)
        self.assertIn("REJECTED", rendered)
        self.assertIn("ANTI_CHASE_HARD", rendered)
        self.assertEqual(_money(None), "N/A")
        self.assertEqual(_money(Decimal("999")), "$999.00")
        self.assertEqual(_money(Decimal("1250")), "$1.2K")
        self.assertEqual(_money(Decimal("2500000")), "$2.50M")

    def test_model_validation_and_snapshot_age(self) -> None:
        with self.assertRaises(ValueError):
            TokenCall("A" * 44, "X", None, datetime(2026, 1, 1))
        with self.assertRaises(ValueError):
            TokenCall("A" * 44, "X", None, self.at, top10_pct=Decimal("101"))
        self.assertEqual(self.snapshot().age_seconds(self.at - timedelta(seconds=1)), 0.0)


if __name__ == "__main__":
    unittest.main()
