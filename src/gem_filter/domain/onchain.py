from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BuyVerification:
    verified: bool
    reason: str
    token_amount: float | None = None
    native_spent_lamports: int | None = None


def verify_enhanced_swap_buy(
    event: dict[str, Any], *, wallet: str, contract: str
) -> BuyVerification:
    """Verify that a Helius enhanced event is a real swap buy, not an airdrop/self-transfer."""
    if str(event.get("type", "")).upper() != "SWAP":
        return BuyVerification(False, "NOT_SWAP")
    if event.get("transactionError") not in (None, False):
        return BuyVerification(False, "FAILED_TRANSACTION")

    incoming = []
    for transfer in event.get("tokenTransfers") or []:
        if (
            transfer.get("mint") == contract
            and transfer.get("toUserAccount") == wallet
            and transfer.get("fromUserAccount") != wallet
            and float(transfer.get("tokenAmount") or 0) > 0
        ):
            incoming.append(float(transfer["tokenAmount"]))
    if not incoming:
        return BuyVerification(False, "NO_INCOMING_TOKEN_BALANCE")

    outgoing = []
    for transfer in event.get("nativeTransfers") or []:
        if (
            transfer.get("fromUserAccount") == wallet
            and transfer.get("toUserAccount") != wallet
            and int(transfer.get("amount") or 0) > 0
        ):
            outgoing.append(int(transfer["amount"]))
    if not outgoing:
        return BuyVerification(False, "NO_OUTGOING_NATIVE_BALANCE")

    return BuyVerification(
        True,
        "VERIFIED_SWAP_BUY",
        token_amount=sum(incoming),
        native_spent_lamports=sum(outgoing),
    )
