from __future__ import annotations

from .models import Decision, TokenStatus

_ALLOWED: dict[TokenStatus, set[TokenStatus]] = {
    TokenStatus.RECEIVED: {TokenStatus.PARSED, TokenStatus.REJECTED},
    TokenStatus.PARSED: {TokenStatus.VERIFYING, TokenStatus.REJECTED},
    TokenStatus.VERIFYING: {
        TokenStatus.WATCH,
        TokenStatus.EARLY,
        TokenStatus.STRONG,
        TokenStatus.REJECTED,
    },
    TokenStatus.WATCH: {
        TokenStatus.EARLY,
        TokenStatus.STRONG,
        TokenStatus.CONFIRMED,
        TokenStatus.EXPIRED,
        TokenStatus.REJECTED,
        TokenStatus.CLOSED,
    },
    TokenStatus.EARLY: {
        TokenStatus.STRONG,
        TokenStatus.CONFIRMED,
        TokenStatus.EXPIRED,
        TokenStatus.REJECTED,
        TokenStatus.CLOSED,
    },
    TokenStatus.STRONG: {
        TokenStatus.CONFIRMED,
        TokenStatus.EXPIRED,
        TokenStatus.REJECTED,
        TokenStatus.CLOSED,
    },
    TokenStatus.CONFIRMED: {TokenStatus.CLOSED},
    TokenStatus.EXPIRED: {TokenStatus.CLOSED},
    TokenStatus.REJECTED: {TokenStatus.CLOSED},
    TokenStatus.CLOSED: set(),
}


def transition(current: TokenStatus, target: TokenStatus, *, replay: bool = False) -> TokenStatus:
    if current == target:
        return current
    if replay or target in _ALLOWED[current]:
        return target
    raise ValueError(f"invalid token state transition: {current} -> {target}")


def status_for_decision(decision: Decision) -> TokenStatus | None:
    mapping = {
        Decision.WATCH: TokenStatus.WATCH,
        Decision.EARLY: TokenStatus.EARLY,
        Decision.STRONG: TokenStatus.STRONG,
        Decision.CONFIRMED: TokenStatus.CONFIRMED,
        Decision.EXPIRED: TokenStatus.EXPIRED,
        Decision.REJECTED: TokenStatus.REJECTED,
    }
    return mapping.get(decision)
