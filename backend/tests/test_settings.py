from dataclasses import replace

import pytest

from backend.shared.settings import load_settings


def test_test_settings_are_loaded_from_yaml() -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")

    assert settings.database.port == 55433
    assert settings.rate_limit.enabled is False
    assert settings.auth.access_token_ttl_seconds == 86_400
    assert settings.auth.refresh_token_ttl_seconds == 604_800
    # Паузы и ретраи 429 переехали на шлюз; у воркера остались лимиты задач.
    assert settings.worker.job_lease_seconds == 1800
    assert settings.worker.job_retry_backoff_seconds == 300
    # Шлюз wb-egress: своя секция вместо прежних security/wb_api.
    assert settings.egress.audience == "wb-egress:karbi"
    assert settings.egress.jwt_ttl_seconds == 300
    assert settings.egress.request_timeout_seconds == 200


def test_a_request_timeout_below_the_poll_timeout_is_refused() -> None:
    """Long polling holds the connection open, so the request must outlive the poll."""
    settings = load_settings("backend/shared/settings/config.test.yaml")
    broken = replace(settings, telegram=replace(settings.telegram, request_timeout_seconds=1, poll_timeout_seconds=25))

    with pytest.raises(ValueError, match="request_timeout_seconds"):
        broken.validate_values()
