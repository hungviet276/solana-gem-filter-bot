from gem_filter.config import Settings


def test_blank_optional_environment_values_are_none() -> None:
    settings = Settings(
        _env_file=None,
        telegram_api_id="",
        telegram_bot_token=" ",
        telegram_api_hash="",
        alert_bot_token="",
        market_fallback_base_url="",
        helius_webhook_secret="",
    )

    assert settings.telegram_api_id is None
    assert settings.telegram_bot_token is None
    assert settings.telegram_api_hash is None
    assert settings.alert_bot_token is None
    assert settings.market_fallback_base_url is None
    assert settings.helius_webhook_secret is None


def test_early_candidate_x2_destinations_are_independent() -> None:
    settings = Settings(
        _env_file=None,
        alert_destination_chat_ids="-100111",
        early_candidate_x2_enabled=True,
        early_candidate_x2_destination_chat_ids=" -100222, -100333 ",
    )

    assert settings.alert_chat_ids == ("-100111",)
    assert settings.early_candidate_x2_chat_ids == ("-100222", "-100333")
