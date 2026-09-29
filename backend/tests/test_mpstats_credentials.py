import pytest
from pydantic import ValidationError

from backend.commands.sync_egress_status import _all_disabled, _states
from backend.modules.wb_core.domain import MARKETPLACES
from backend.modules.wb_core.infrastructure.postgres.repository import _EGRESS_COLUMNS
from backend.modules.wb_core.presentation.http.schemas import MPStatsCredentials, SellerResponse


def test_the_token_is_trimmed_and_kept_out_of_the_repr() -> None:
    payload = MPStatsCredentials.model_validate({"token": "  aaaa1111.bbbb2222\n"})

    assert payload.token.get_secret_value() == "aaaa1111.bbbb2222"
    assert "aaaa1111" not in repr(payload)


@pytest.mark.parametrize("token", ["", "short", "aaaa1111 bbbb2222", "aaaa1111\nbbbb2222"])
def test_a_token_pasted_in_pieces_is_refused(token: str) -> None:
    with pytest.raises(ValidationError):
        MPStatsCredentials.model_validate({"token": token})


def test_the_response_never_carries_a_token() -> None:
    assert not [name for name in SellerResponse.model_fields if "token" in name or "key" in name]


def test_every_known_credential_has_columns_to_land_in() -> None:
    """Сверка пишет исход по имени учётки: имя без колонок уронило бы её целиком."""
    assert set(_EGRESS_COLUMNS) == set(MARKETPLACES)


def test_the_gateway_state_is_read_per_credential() -> None:
    row = {
        "status": "verified",
        "marketplaces": {
            "wb": {"status": "verified", "verify_error": ""},
            "mpstats": {"status": "key_invalid", "verify_error": "HTTP 401 от MPStats"},
        },
    }

    assert _states(row) == {"wb": ("verified", None), "mpstats": ("key_invalid", "HTTP 401 от MPStats")}


def test_a_live_mpstats_token_keeps_the_seller_from_counting_as_put_out() -> None:
    """Сирота на шлюзе гасится, пока жива хоть одна учётка — в том числе токен MPStats."""
    row = {"marketplaces": {"wb": {"status": "disabled"}, "mpstats": {"status": "verified"}}}

    assert not _all_disabled(row)
    row["marketplaces"]["mpstats"]["status"] = "disabled"
    assert _all_disabled(row)
