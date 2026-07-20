from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from gem_filter.application.live_evaluation import evaluate_live
from gem_filter.application.processor import ProcessingService
from gem_filter.domain.models import Decision, MarketSnapshot, TokenCall, WhaleEvent
from gem_filter.domain.rules import DEFAULT_RULE, evaluate
from gem_filter.infrastructure.database import RawTelegramEvent, TelegramSource, WhaleEventRecord


def test_live_evaluation_keeps_snapshot_captured_after_request_started() -> None:
    snapshot_time = datetime.now(tz=UTC)
    request_started_at = snapshot_time - timedelta(seconds=1)
    token = TokenCall(
        contract="A" * 44,
        ticker="GEM",
        token_name="Gem",
        call_ts=snapshot_time - timedelta(minutes=2),
        call_mc_usd=Decimal("50000"),
        age_seconds=120,
        holders=383,
        top10_pct=Decimal("23"),
        bundled_pct=Decimal("10"),
        first50_pct=Decimal("9.3"),
        jeeters_pct=Decimal("8.6"),
        quickflip_pct=Decimal("0"),
        snipers_pct=Decimal("3"),
        insiders_pct=Decimal("0"),
        dev_pct=Decimal("0"),
        safe_pct=Decimal("43"),
        poor_pct=Decimal("5"),
    )
    market = MarketSnapshot(
        captured_at=snapshot_time,
        provider_ts=snapshot_time,
        provider="test",
        pair_address="pair",
        dex="test",
        market_cap_usd=Decimal("50000"),
        fdv_usd=Decimal("50000"),
        liquidity_usd=Decimal("10000"),
    )
    whales = (
        WhaleEvent(
            label="FEBU",
            normalized_label="febu",
            event_ts=snapshot_time - timedelta(minutes=1),
            sol_amount=Decimal("4.37"),
            whale_mc_usd=Decimal("50000"),
        ),
    )

    old_result = evaluate(
        token,
        market,
        whales,
        (),
        DEFAULT_RULE,
        evaluation_time=request_started_at,
    )
    live_result = evaluate_live(token, market, whales, (), DEFAULT_RULE)

    assert old_result.decision is Decision.REJECTED
    assert "MARKET_STALE" in old_result.reason_codes
    assert live_result.decision is Decision.STRONG
    assert "MARKET_STALE" not in live_result.reason_codes


def source() -> TelegramSource:
    return TelegramSource(
        id=uuid.uuid4(),
        name="source",
        channel_id=-100123,
        connection_mode="mtproto",
        parser_version="test",
        enabled=True,
    )


def raw_event(
    telegram_source: TelegramSource,
    *,
    raw_id: uuid.UUID,
    revision: int,
    event_kind: str,
) -> RawTelegramEvent:
    return RawTelegramEvent(
        id=raw_id,
        source_id=telegram_source.id,
        source_message_id=501,
        reply_to_message_id=None,
        event_kind=event_kind,
        revision=revision,
        event_ts=datetime(2026, 7, 20, tzinfo=UTC),
        raw_text="$ANSEM whale bought 4.2 SOL",
        raw_text_hash="a" * 64,
        telegram_payload={},
    )


async def test_process_whale_creates_one_logical_message_identity() -> None:
    telegram_source = source()
    raw = raw_event(
        telegram_source,
        raw_id=uuid.uuid4(),
        revision=0,
        event_kind="NEW",
    )
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[None, None])
    session.flush = AsyncMock()
    service = ProcessingService(MagicMock(), MagicMock(), MagicMock())

    result = await service._process_whale(
        session,
        telegram_source,
        raw,
        {
            "whale_label": "ANSEM",
            "normalized_label": "ansem",
            "sol_amount": "4.1",
            "whale_mc_usd": "67400",
        },
    )

    event = session.add.call_args.args[0]
    assert isinstance(event, WhaleEventRecord)
    assert event.source_id == telegram_source.id
    assert event.source_message_id == 501
    assert event.sol_amount == Decimal("4.1")
    assert result["business_status"] == "orphan"


async def test_process_whale_edit_updates_instead_of_inserting_duplicate() -> None:
    telegram_source = source()
    original_raw = raw_event(
        telegram_source,
        raw_id=uuid.uuid4(),
        revision=0,
        event_kind="NEW",
    )
    edited_raw = raw_event(
        telegram_source,
        raw_id=uuid.uuid4(),
        revision=100,
        event_kind="EDITED",
    )
    existing = WhaleEventRecord(
        id=uuid.uuid4(),
        raw_event_id=original_raw.id,
        source_id=telegram_source.id,
        source_message_id=501,
        token_call_id=None,
        whale_label="ANSEM",
        normalized_label="ansem",
        sol_amount=Decimal("4.1"),
        whale_mc_usd=Decimal("67400"),
        event_ts=original_raw.event_ts,
        verification_status="VERIFIED",
        signature="verified-signature",
        orphan_reason="CALL_NOT_FOUND_BY_REPLY_TO",
    )
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[existing, None])
    session.get = AsyncMock(return_value=original_raw)
    session.flush = AsyncMock()
    service = ProcessingService(MagicMock(), MagicMock(), MagicMock())

    result = await service._process_whale(
        session,
        telegram_source,
        edited_raw,
        {
            "whale_label": "ANSEM",
            "normalized_label": "ansem",
            "sol_amount": "4.2",
            "whale_mc_usd": "67400",
        },
    )

    session.add.assert_not_called()
    assert existing.raw_event_id == edited_raw.id
    assert existing.sol_amount == Decimal("4.2")
    assert existing.verification_status == "VERIFIED"
    assert existing.signature == "verified-signature"
    assert result["whale_event_id"] == str(existing.id)


async def test_process_whale_does_not_replace_newer_revision_with_stale_one() -> None:
    telegram_source = source()
    stale_raw = raw_event(
        telegram_source,
        raw_id=uuid.uuid4(),
        revision=0,
        event_kind="NEW",
    )
    latest_raw = raw_event(
        telegram_source,
        raw_id=uuid.uuid4(),
        revision=100,
        event_kind="EDITED",
    )
    existing = WhaleEventRecord(
        id=uuid.uuid4(),
        raw_event_id=latest_raw.id,
        source_id=telegram_source.id,
        source_message_id=501,
        token_call_id=None,
        whale_label="ANSEM",
        normalized_label="ansem",
        sol_amount=Decimal("4.2"),
        whale_mc_usd=Decimal("67400"),
        event_ts=latest_raw.event_ts,
        verification_status="NOT_REQUESTED",
    )
    session = MagicMock()
    session.scalar = AsyncMock(return_value=existing)
    session.get = AsyncMock(return_value=latest_raw)
    service = ProcessingService(MagicMock(), MagicMock(), MagicMock())

    result = await service._process_whale(
        session,
        telegram_source,
        stale_raw,
        {
            "whale_label": "ANSEM",
            "normalized_label": "ansem",
            "sol_amount": "4.1",
            "whale_mc_usd": "67400",
        },
    )

    assert result["business_status"] == "stale_revision"
    assert existing.raw_event_id == latest_raw.id
