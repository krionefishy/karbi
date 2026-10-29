from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.modules.wb_screenshot_control.infrastructure.wiring import log_startup_state
from backend.shared.settings import Settings, load_settings


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


def test_screenshot_control_comes_from_docker_yaml_with_keys_out_of_the_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SCREENSHOT_CONTROL_MODE", "SCREENSHOT_CONTROL_S3_BUCKET", "VISION_MODEL", "VISION_FALLBACK_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SCREENSHOT_CONTROL_HMAC_KEY", "hmac-secret-that-is-long-enough-0123456789")
    monkeypatch.setenv("SCREENSHOT_CONTROL_TELEGRAM", "false")
    load_settings.cache_clear()

    settings = load_settings("backend/shared/settings/config.docker.yaml")
    load_settings.cache_clear()

    screens = settings.screenshot_control
    assert screens.mode == "shadow" and screens.telegram_enabled is False
    assert (screens.retention_original_days, screens.retention_thumb_days) == (365, 365)
    assert screens.thresholds.text_dup == 0.90 and screens.thresholds.min_text_len == 25
    assert screens.staging_root == "/data/screenshots-staging"
    assert screens.allowed_url_prefixes == ("https://seller.wildberries.ru/chat-with-clients",)
    assert "hmac-secret" not in repr(screens) and "hmac-secret" not in repr(settings)
    assert settings.vision.model == "Qwen/Qwen3.6-35B-A3B" and settings.vision.max_tokens == 1200
    assert settings.vision.fallback_model == "Qwen/Qwen3.5-397B-A17B" and settings.vision.enable_thinking is False


def test_the_module_bucket_falls_back_to_the_shared_one() -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")
    shared = replace(settings.s3, bucket="shared-bucket")

    assert settings.screenshot_control.bucket(shared) == "shared-bucket"
    assert replace(settings.screenshot_control, s3_bucket="dbs-pdn").bucket(shared) == "dbs-pdn"


@pytest.mark.parametrize("mode", ["", "on", "SHADOW", "enforced"])
def test_an_unknown_screenshot_mode_is_refused(mode: str) -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")
    broken = replace(settings, screenshot_control=replace(settings.screenshot_control, mode=mode))

    with pytest.raises(ValueError, match="screenshot_control.mode"):
        broken.validate_values()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("kpi_from", "23.10.2026", "kpi_from"),
        ("retention_original_days", 0, "positive"),
        ("retention_fields_days", 30, "retention_fields_days"),
        ("extension_min_version", "v1", "extension_min_version"),
        ("allowed_url_prefixes", ("http://seller.wildberries.ru",), "allowed_url_prefixes"),
        ("purge_hour", 24, "purge_hour"),
    ],
)
def test_broken_screenshot_settings_are_refused(field: str, value: Any, message: str) -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")
    broken = replace(settings, screenshot_control=replace(settings.screenshot_control, **{field: value}))

    with pytest.raises(ValueError, match=message):
        broken.validate_values()


def _raw_test_config() -> dict[str, Any]:
    raw = yaml.safe_load(Path("backend/shared/settings/config.test.yaml").read_text(encoding="utf-8"))
    return dict(raw)


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
@pytest.mark.parametrize("key", ["", "short-key"])
def test_without_the_hmac_key_only_the_screenshot_module_turns_off(mode: str, key: str) -> None:
    raw = _raw_test_config()
    raw["screenshot_control"] = {**raw["screenshot_control"], "mode": mode, "hmac_key": key}

    settings = Settings.from_dict(raw)

    screens = settings.screenshot_control
    assert (screens.mode, screens.disabled_reason, screens.requested_mode) == ("off", "hmac_key_missing", mode)
    production = replace(settings, app=replace(settings.app, environment="production"))
    with pytest.raises(ValueError) as error:  # тестовый конфиг небезопасен для прода по другим причинам
        production.validate_runtime_secrets()
    assert "screenshot_control" not in str(error.value) and "hmac" not in str(error.value)
    assert log_startup_state(settings, worker=True) == ["screenshot_control_disabled"]


def test_a_long_hmac_key_keeps_the_requested_mode() -> None:
    raw = _raw_test_config()
    raw["screenshot_control"] = {**raw["screenshot_control"], "mode": "enforce", "hmac_key": "k" * 32}
    screens = Settings.from_dict(raw).screenshot_control
    assert (screens.mode, screens.disabled_reason) == ("enforce", "")


def test_the_worker_warns_without_the_model_key() -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")
    keyless = replace(settings, vision=replace(settings.vision, api_key=""))
    assert log_startup_state(keyless, worker=True) == ["screenshot_control_model_key_missing"]
    assert log_startup_state(keyless, worker=False) == []
    off = replace(keyless, screenshot_control=replace(keyless.screenshot_control, mode="off"))
    assert log_startup_state(off, worker=True) == []


def test_an_empty_vision_model_from_compose_keeps_the_chosen_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_MODEL", "")
    monkeypatch.setenv("SCREENSHOT_CONTROL_HMAC_KEY", "hmac-secret-that-is-long-enough-0123456789")
    load_settings.cache_clear()

    settings = load_settings("backend/shared/settings/config.docker.yaml")
    load_settings.cache_clear()

    assert settings.vision.model == "Qwen/Qwen3.6-35B-A3B"
