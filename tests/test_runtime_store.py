from datetime import UTC, datetime

from gem_filter.infrastructure.runtime_store import OFFSET_KEY, dedupe_key, rate_key


def test_runtime_state_keys_are_deterministic_and_bounded() -> None:
    assert OFFSET_KEY == "reader:bot_api:offset"
    assert dedupe_key("helius", "signature-1") == "dedupe:helius:signature-1"
    assert len(dedupe_key("helius", "x" * 400)) == 255


def test_rate_key_uses_utc_minute_bucket() -> None:
    timestamp = datetime(2026, 7, 16, 3, 45, 59, 999999, tzinfo=UTC)
    assert rate_key("telegram:-1001", timestamp) == "rate:telegram:-1001:202607160345"
