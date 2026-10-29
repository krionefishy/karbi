from dataclasses import replace

import pytest

from backend.shared.settings import load_settings


def test_test_settings_are_loaded_from_yaml() -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")

    assert settings.database.port == 55433
    assert settings.rate_limit.enabled is False
    assert settings.auth.access_token_ttl_seconds == 86_400
    assert settings.auth.refresh_token_ttl_seconds == 604_800
    assert settings.worker.job_lease_seconds == 1800
    assert settings.worker.job_retry_backoff_seconds == 300
    assert settings.egress.audience == "wb-egress:karbi"
    assert settings.egress.jwt_ttl_seconds == 300
    assert settings.egress.request_timeout_seconds == 200


def test_a_request_timeout_below_the_poll_timeout_is_refused() -> None:
    """Long polling holds the connection open, so the request must outlive the poll."""
    settings = load_settings("backend/shared/settings/config.test.yaml")
    broken = replace(settings, telegram=replace(settings.telegram, request_timeout_seconds=1, poll_timeout_seconds=25))

    with pytest.raises(ValueError, match="request_timeout_seconds"):
        broken.validate_values()


def test_the_model_comes_from_config_and_the_key_stays_out_of_the_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "Other/Model-7B")
    monkeypatch.setenv("FOUNDATION_MODELS_API_KEY", "super-secret-key")
    load_settings.cache_clear()

    settings = load_settings("backend/shared/settings/config.docker.yaml")
    load_settings.cache_clear()

    assert settings.llm.model == "Other/Model-7B"
    assert settings.llm.base_url == "https://foundation-models.api.cloud.ru/v1"
    assert settings.llm.api_key == "super-secret-key" and "super-secret-key" not in repr(settings.llm)
    assert settings.llm.temperature == 0.0 and settings.llm.max_tokens == 150


def test_the_default_model_is_the_one_chosen_for_the_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_MODEL", raising=False)
    load_settings.cache_clear()

    settings = load_settings("backend/shared/settings/config.docker.yaml")
    load_settings.cache_clear()

    assert settings.llm.model == "Qwen/Qwen3-30B-A3B"
    assert settings.support_bot.classifier_mode == "enforce"


@pytest.mark.parametrize("mode", ["", "on", "ENFORCE", "shadows"])
def test_an_unknown_classifier_mode_is_refused(mode: str) -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")
    broken = replace(settings, support_bot=replace(settings.support_bot, classifier_mode=mode))

    with pytest.raises(ValueError, match="classifier_mode"):
        broken.validate_values()


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_known_classifier_modes_are_accepted(mode: str) -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")

    replace(settings, support_bot=replace(settings.support_bot, classifier_mode=mode)).validate_values()
