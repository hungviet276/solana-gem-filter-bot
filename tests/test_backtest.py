from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from gem_filter.application.backtest import ReplayCase, run_backtest
from gem_filter.domain.models import MarketSnapshot, MultipleEvent, TokenCall, WhaleEvent


class BacktestTests(unittest.TestCase):
    def test_replay_reports_sample_size_and_precision(self) -> None:
        at = datetime(2026, 7, 1, tzinfo=UTC)
        token = TokenCall(
            contract="A" * 44,
            ticker="GEM",
            token_name="Gem",
            call_ts=at,
            call_mc_usd=Decimal("10000"),
            age_seconds=60,
            holders=100,
            top10_pct=Decimal("18"),
            bundled_pct=Decimal("3"),
            first50_pct=Decimal("60"),
            jeeters_pct=Decimal("4"),
            quickflip_pct=Decimal("2"),
            snipers_pct=Decimal("1"),
            insiders_pct=Decimal("2"),
            safe_pct=Decimal("40"),
            dev_pct=Decimal("0"),
            poor_pct=Decimal("3"),
        )
        snapshot = MarketSnapshot(
            at, at, "test", "pair", "raydium", Decimal("10000"), Decimal("10000"), Decimal("10000")
        )
        whale = WhaleEvent(
            "Opal", "opal", at + timedelta(minutes=2), Decimal("12"), Decimal("11000")
        )
        whale_snapshot = MarketSnapshot(
            whale.event_ts,
            whale.event_ts,
            "test",
            "pair",
            "raydium",
            Decimal("11000"),
            Decimal("11000"),
            Decimal("10000"),
        )
        outcome = MultipleEvent(5, at + timedelta(minutes=20), Decimal("50000"), 1200)
        result = run_backtest([ReplayCase(token, (snapshot, whale_snapshot), (whale,), (outcome,))])
        self.assertEqual(result.samples, 1)
        self.assertEqual(result.alerted, 1)
        self.assertEqual(result.precision_x2, 1.0)
        self.assertEqual(result.precision_x5, 1.0)
        self.assertIsNotNone(result.warning)


if __name__ == "__main__":
    unittest.main()
