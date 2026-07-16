from __future__ import annotations

import unittest

from gem_filter.domain.onchain import verify_enhanced_swap_buy


class OnchainTests(unittest.TestCase):
    def test_real_swap_buy_requires_token_in_and_native_out(self) -> None:
        event = {
            "type": "SWAP",
            "transactionError": None,
            "tokenTransfers": [
                {
                    "mint": "TOKEN",
                    "fromUserAccount": "POOL",
                    "toUserAccount": "WHALE",
                    "tokenAmount": 42,
                }
            ],
            "nativeTransfers": [
                {"fromUserAccount": "WHALE", "toUserAccount": "POOL", "amount": 1_000_000_000}
            ],
        }
        result = verify_enhanced_swap_buy(event, wallet="WHALE", contract="TOKEN")
        self.assertTrue(result.verified)
        self.assertEqual(result.reason, "VERIFIED_SWAP_BUY")

    def test_airdrop_or_self_transfer_is_not_buy(self) -> None:
        airdrop = {
            "type": "TRANSFER",
            "tokenTransfers": [
                {
                    "mint": "TOKEN",
                    "fromUserAccount": "A",
                    "toUserAccount": "WHALE",
                    "tokenAmount": 42,
                }
            ],
            "nativeTransfers": [],
        }
        self.assertFalse(
            verify_enhanced_swap_buy(airdrop, wallet="WHALE", contract="TOKEN").verified
        )
        self_transfer = {
            "type": "SWAP",
            "transactionError": None,
            "tokenTransfers": [
                {
                    "mint": "TOKEN",
                    "fromUserAccount": "WHALE",
                    "toUserAccount": "WHALE",
                    "tokenAmount": 42,
                }
            ],
            "nativeTransfers": [
                {"fromUserAccount": "WHALE", "toUserAccount": "WHALE", "amount": 1}
            ],
        }
        self.assertFalse(
            verify_enhanced_swap_buy(self_transfer, wallet="WHALE", contract="TOKEN").verified
        )

    def test_failed_swap_and_missing_native_outflow_are_rejected(self) -> None:
        failed = {"type": "SWAP", "transactionError": {"code": 1}}
        self.assertEqual(
            verify_enhanced_swap_buy(failed, wallet="W", contract="T").reason,
            "FAILED_TRANSACTION",
        )
        no_native = {
            "type": "SWAP",
            "transactionError": None,
            "tokenTransfers": [
                {
                    "mint": "T",
                    "fromUserAccount": "POOL",
                    "toUserAccount": "W",
                    "tokenAmount": 1,
                }
            ],
            "nativeTransfers": [],
        }
        self.assertEqual(
            verify_enhanced_swap_buy(no_native, wallet="W", contract="T").reason,
            "NO_OUTGOING_NATIVE_BALANCE",
        )


if __name__ == "__main__":
    unittest.main()
