from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from gem_filter.application.processor import ProcessingService
from gem_filter.config import Settings
from gem_filter.domain.alerts import EARLY_DISCLAIMER, render_early_candidate_x2
from gem_filter.domain.early_candidate import (
    EARLY_CANDIDATE_X2_LEVEL,
    EARLY_CANDIDATE_X2_RULE_VERSION,
    evaluate_early_candidate_x2,
)
from gem_filter.domain.models import TokenCall
from gem_filter.infrastructure.database import (
    AlertRecord,
    OutboxEvent,
    ScoreEvaluationRecord,
    TokenCallRecord,
)


def matching_token(**overrides: object) -> TokenCall:
    values: dict[str, object] = {
        "contract": "A" * 44,
        "ticker": "GEM",
        "token_name": "Gem",
        "call_ts": datetime(2026, 7, 20, tzinfo=UTC),
        "source_message_id": 123,
        "call_mc_usd": Decimal("42000"),
        "age_seconds": 720,
        "holders": 350,
        "top10_pct": Decimal("23"),
        "bundled_pct": Decimal("10"),
        "jeeters_pct": Decimal("12"),
        "snipers_pct": Decimal("3"),
        "dev_pct": Decimal("0"),
        "poor_pct": Decimal("7"),
    }
    values.update(overrides)
    return TokenCall(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("jeeters_pct", Decimal("9.99")),
        ("jeeters_pct", Decimal("20.01")),
        ("poor_pct", Decimal("15.01")),
        ("top10_pct", Decimal("19.99")),
        ("top10_pct", Decimal("30.01")),
        ("dev_pct", Decimal("10")),
        ("bundled_pct", Decimal("25")),
        ("snipers_pct", Decimal("15")),
        ("poor_pct", None),
    ],
)
def test_early_candidate_rejects_failed_or_missing_initial_field(
    field: str, value: Decimal | None
) -> None:
    result = evaluate_early_candidate_x2(matching_token(**{field: value}))

    assert result.matched is False
    assert any(not check.passed for check in result.checks)


def test_early_candidate_matches_boundaries_and_is_deterministic() -> None:
    token = matching_token(
        jeeters_pct=Decimal("10"),
        poor_pct=Decimal("15"),
        top10_pct=Decimal("30"),
    )

    first = evaluate_early_candidate_x2(token)
    second = evaluate_early_candidate_x2(token)

    assert first.matched is True
    assert all(check.passed for check in first.checks)
    assert first.rule_version == EARLY_CANDIDATE_X2_RULE_VERSION
    assert first.input_hash == second.input_hash


def test_early_candidate_alert_is_explicitly_call_only() -> None:
    token = matching_token()
    evaluation = evaluate_early_candidate_x2(token)

    rendered = render_early_candidate_x2(token, evaluation)

    assert "EARLY CANDIDATE X2 — CALL ĐẦU" in rendered
    assert "Chưa có xác nhận whale" in rendered
    assert "Jeeters 10–20%" in rendered
    assert "Dev dưới 10%" in rendered
    assert "Bundled dưới 25%" in rendered
    assert "Snipers dưới 15%" in rendered
    assert "<10%" not in rendered
    assert "<25%" not in rendered
    assert "<15%" not in rendered
    assert "31/56 call đạt x2 (55,36%)" in rendered
    assert EARLY_DISCLAIMER in rendered


async def test_early_candidate_uses_dedicated_destination_level_and_outbox() -> None:
    domain_token = matching_token()
    token = _record_from(domain_token)
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[None, None])
    session.flush = AsyncMock()
    settings = Settings(
        _env_file=None,
        early_candidate_x2_enabled=True,
        early_candidate_x2_destination_chat_ids="-100999",
    )
    market_provider = MagicMock()
    service = ProcessingService(MagicMock(), market_provider, settings)

    queued = await service._queue_early_candidate_x2(session, token)

    added = [call.args[0] for call in session.add.call_args_list]
    score = next(item for item in added if isinstance(item, ScoreEvaluationRecord))
    alert = next(item for item in added if isinstance(item, AlertRecord))
    outbox = next(item for item in added if isinstance(item, OutboxEvent))
    assert queued is True
    assert score.rule_version == EARLY_CANDIDATE_X2_RULE_VERSION
    assert score.decision == EARLY_CANDIDATE_X2_LEVEL
    assert score.market_snapshot_id is None
    assert alert.level == EARLY_CANDIDATE_X2_LEVEL
    assert alert.destination_chat_id == "-100999"
    assert outbox.topic == "TELEGRAM_EARLY_X2_ALERT"
    market_provider.fetch.assert_not_called()


async def test_early_candidate_does_not_enter_legacy_flow_when_rule_fails() -> None:
    token = _record_from(matching_token(jeeters_pct=Decimal("9")))
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    session.flush = AsyncMock()
    settings = Settings(
        _env_file=None,
        early_candidate_x2_enabled=True,
        early_candidate_x2_destination_chat_ids="-100999",
    )
    service = ProcessingService(MagicMock(), MagicMock(), settings)

    queued = await service._queue_early_candidate_x2(session, token)

    assert queued is False
    session.add.assert_not_called()


async def test_early_candidate_send_gate_does_not_load_market_whales_or_outcomes() -> None:
    token = _record_from(matching_token())
    alert = AlertRecord(
        id=uuid.uuid4(),
        token_call_id=token.id,
        score_evaluation_id=uuid.uuid4(),
        destination_chat_id="-100999",
        level=EARLY_CANDIDATE_X2_LEVEL,
        status="PENDING",
        rendered_text="queued",
    )
    session = MagicMock()
    session.get = AsyncMock(side_effect=[alert, token])
    session.scalar = AsyncMock(return_value=None)
    session.scalars = AsyncMock()
    session.begin.return_value = _AsyncContext(None)
    sessions = MagicMock(return_value=_AsyncContext(session))
    market_provider = MagicMock()
    settings = Settings(
        _env_file=None,
        early_candidate_x2_enabled=True,
        early_candidate_x2_destination_chat_ids="-100999",
    )
    service = ProcessingService(sessions, market_provider, settings)

    gate = await service.prepare_early_candidate_x2_alert(alert.id)

    assert gate is not None
    assert gate["level"] == EARLY_CANDIDATE_X2_LEVEL
    assert gate["chart_url"] == f"https://dexscreener.com/solana/{token.contract}"
    market_provider.fetch.assert_not_called()
    session.scalars.assert_not_called()


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self.value = value

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, *args: object) -> None:
        return None


def _record_from(token: TokenCall) -> TokenCallRecord:
    return TokenCallRecord(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        source_message_id=token.source_message_id,
        contract=token.contract,
        ticker=token.ticker,
        token_name=token.token_name,
        call_ts=token.call_ts,
        call_mc_usd=token.call_mc_usd,
        age_seconds=token.age_seconds,
        holders=token.holders,
        top10_pct=token.top10_pct,
        bundled_pct=token.bundled_pct,
        first50_pct=token.first50_pct,
        jeeters_pct=token.jeeters_pct,
        quickflip_pct=token.quickflip_pct,
        snipers_pct=token.snipers_pct,
        insiders_pct=token.insiders_pct,
        dev_pct=token.dev_pct,
        safe_pct=token.safe_pct,
        poor_pct=token.poor_pct,
        rule_version="GEM_FILTER_V2_FLOW_8",
        dataset_split="OUT_OF_SAMPLE",
        status="VERIFYING",
        state_reason="CALL_PARSED",
        expires_at=token.call_ts + timedelta(hours=6),
    )
