from backend.shared.settings import load_settings


def test_test_settings_are_loaded_from_yaml() -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")

    assert settings.database.port == 55433
    assert settings.rate_limit.enabled is False
    assert settings.auth.access_token_ttl_seconds == 86_400
    assert settings.auth.refresh_token_ttl_seconds == 604_800
    assert settings.worker.feedback_request_interval_seconds == 0.0
    assert settings.worker.feedback_retry_wait_seconds == 600
