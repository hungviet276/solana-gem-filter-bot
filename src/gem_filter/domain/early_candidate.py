from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from .models import TokenCall

EARLY_CANDIDATE_X2_LEVEL = "EARLY_CANDIDATE_X2"
EARLY_CANDIDATE_X2_RULE_VERSION = "EARLY_CANDIDATE_X2_V1"


@dataclass(frozen=True, slots=True)
class EarlyCandidateCheck:
    code: str
    passed: bool
    actual: Decimal | None
    condition: str

    def as_record(self) -> dict[str, object]:
        return {
            "code": self.code,
            "passed": self.passed,
            "evidence": {
                "actual": str(self.actual) if self.actual is not None else None,
                "condition": self.condition,
                "source": "INITIAL_CALL_ONLY",
            },
        }


@dataclass(frozen=True, slots=True)
class EarlyCandidateEvaluation:
    matched: bool
    checks: tuple[EarlyCandidateCheck, ...]
    input_hash: str
    rule_version: str = EARLY_CANDIDATE_X2_RULE_VERSION


def evaluate_early_candidate_x2(token: TokenCall) -> EarlyCandidateEvaluation:
    """Evaluate only immutable fields parsed from the first call message."""

    checks = (
        _between("JEETERS_10_20", token.jeeters_pct, Decimal("10"), Decimal("20")),
        _at_most("POOR_MAX_15", token.poor_pct, Decimal("15")),
        _between("TOP10_20_30", token.top10_pct, Decimal("20"), Decimal("30")),
        _below("DEV_BELOW_10", token.dev_pct, Decimal("10")),
        _below("BUNDLED_BELOW_25", token.bundled_pct, Decimal("25")),
        _below("SNIPERS_BELOW_15", token.snipers_pct, Decimal("15")),
    )
    initial_values = {
        "contract": token.contract,
        "source_message_id": token.source_message_id,
        "jeeters_pct": _hash_value(token.jeeters_pct),
        "poor_pct": _hash_value(token.poor_pct),
        "top10_pct": _hash_value(token.top10_pct),
        "dev_pct": _hash_value(token.dev_pct),
        "bundled_pct": _hash_value(token.bundled_pct),
        "snipers_pct": _hash_value(token.snipers_pct),
        "rule_version": EARLY_CANDIDATE_X2_RULE_VERSION,
    }
    input_hash = hashlib.sha256(
        json.dumps(initial_values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return EarlyCandidateEvaluation(
        matched=all(check.passed for check in checks),
        checks=checks,
        input_hash=input_hash,
    )


def _between(
    code: str, value: Decimal | None, minimum: Decimal, maximum: Decimal
) -> EarlyCandidateCheck:
    return EarlyCandidateCheck(
        code=code,
        passed=value is not None and minimum <= value <= maximum,
        actual=value,
        condition=f"{minimum} <= value <= {maximum}",
    )


def _at_most(code: str, value: Decimal | None, maximum: Decimal) -> EarlyCandidateCheck:
    return EarlyCandidateCheck(
        code=code,
        passed=value is not None and value <= maximum,
        actual=value,
        condition=f"value <= {maximum}",
    )


def _below(code: str, value: Decimal | None, maximum: Decimal) -> EarlyCandidateCheck:
    return EarlyCandidateCheck(
        code=code,
        passed=value is not None and value < maximum,
        actual=value,
        condition=f"value < {maximum}",
    )


def _hash_value(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
