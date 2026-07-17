from __future__ import annotations

import unittest

from gem_filter.domain.models import EventType
from gem_filter.domain.parser import (
    normalize_whale_label,
    parse_age_seconds,
    parse_decimal,
    parse_message,
)

CONTRACT = "A" * 44


class ParserTests(unittest.TestCase):
    def test_call_extracts_all_supported_fields(self) -> None:
        raw = f"""🚀 $GEM (Gem Token)
{CONTRACT}
📊 MC: $40K · ⏱ Age: 8m · 👪 Holders: 150
🔟 Top10: 18% · 📦 Bundled: 3% · 🏁 First50: 55%
☢️ Jeeters: 4% · 🔄 QuickFlip: 2% · 🎯 Snipers: 1%
🐁 Insiders: 2% · 👤 Dev: 0%
✅ Safe: 40% · ⚠️ Poor: 3%"""
        result = parse_message(raw)
        self.assertEqual(result.event_type, EventType.CALL)
        self.assertEqual(result.payload["contract"], CONTRACT)
        self.assertEqual(result.payload["ticker"], "GEM")
        self.assertEqual(result.payload["call_mc_usd"], "40000")
        self.assertEqual(result.payload["age_seconds"], 480)
        self.assertEqual(result.payload["holders"], 150)
        self.assertEqual(result.payload["top10_pct"], "18")
        self.assertEqual(result.payload["poor_pct"], "3")
        self.assertEqual(result.warnings, ())

    def test_call_header_keeps_unicode_and_symbol_tickers(self) -> None:
        for ticker in ("©AT", "るのん", "Emote+", "ピングー", "Dog-001"):
            with self.subTest(ticker=ticker):
                result = parse_message(
                    f"🚀 ${ticker} (Token Name)\n{CONTRACT}\n📊 MC: $40K · 👪 Holders: 10"
                )
                self.assertEqual(result.event_type, EventType.CALL)
                self.assertEqual(result.payload["ticker"], ticker.upper())

    def test_missing_field_is_warning_not_zero(self) -> None:
        result = parse_message(f"$GEM\n{CONTRACT}\nMC: $40K\nHolders: 10")
        self.assertNotIn("dev_pct", result.payload)
        self.assertIn("MISSING_DEV_PCT", result.warnings)

    def test_whale_variants_normalize_label(self) -> None:
        result = parse_message("🐋 $Big OPAL whale bought 12.1 SOL @ $48.7K MC")
        self.assertEqual(result.event_type, EventType.WHALE_BUY)
        self.assertEqual(result.payload["normalized_label"], "big_opal")
        self.assertEqual(result.payload["sol_amount"], "12.1")
        self.assertEqual(result.payload["whale_mc_usd"], "48700.0")

    def test_multiple_result_x2_to_x99(self) -> None:
        for multiple in (2, 9, 10, 11, 19, 20, 99, 100):
            with self.subTest(multiple=multiple):
                result = parse_message(f"Just did x{multiple} | Peak $2.5M after 8m")
                self.assertEqual(result.event_type, EventType.MULTIPLE_RESULT)
                self.assertEqual(result.payload["multiple"], multiple)
                self.assertEqual(result.payload["peak_mc_usd"], "2500000.0")

    def test_multiple_arrow_format_extracts_target_mc(self) -> None:
        result = parse_message("🚀 $BUSDY x2 🚀\n💵 MC: $64.1K → $134.3K in 6m")
        self.assertEqual(result.event_type, EventType.MULTIPLE_RESULT)
        self.assertEqual(result.payload["multiple"], 2)
        self.assertEqual(result.payload["peak_mc_usd"], "134300.0")
        self.assertEqual(result.payload["elapsed_seconds"], 360)

    def test_unknown_format_is_preserved_for_review(self) -> None:
        result = parse_message("a format we do not recognize")
        self.assertEqual(result.event_type, EventType.UNKNOWN)
        self.assertIn("UNKNOWN_FORMAT", result.warnings)

    def test_helpers(self) -> None:
        self.assertEqual(parse_decimal("1.25M"), 1_250_000)
        self.assertEqual(parse_decimal("1,234.5K"), 1_234_500)
        self.assertEqual(parse_decimal("1,5B"), 1_500_000_000)
        self.assertEqual(parse_age_seconds("1h 5m"), 3900)
        self.assertEqual(normalize_whale_label("$A.N.S.E.M"), "a_n_s_e_m")
        with self.assertRaises(ValueError):
            parse_decimal("not-a-number")
        with self.assertRaises(ValueError):
            parse_age_seconds("soon")

    def test_service_and_invalid_fields(self) -> None:
        service = parse_message("pinned a message")
        self.assertEqual(service.event_type, EventType.SERVICE)
        invalid = parse_message(f"$GEM\nContract: {CONTRACT}\nMC: nope\nHolders: many\nDev: 120%")
        self.assertEqual(invalid.event_type, EventType.CALL)
        self.assertIn("INVALID_CALL_MC_USD", invalid.warnings)
        self.assertIn("INVALID_HOLDERS", invalid.warnings)
        self.assertIn("INVALID_DEV_PCT", invalid.warnings)

    def test_multiple_without_parseable_elapsed_still_keeps_result(self) -> None:
        result = parse_message("Just did x2 after soon")
        self.assertEqual(result.payload, {"multiple": 2})


if __name__ == "__main__":
    unittest.main()
