"""Расширение: код сопряжения, обмен на токен, проверки токена на каждом запросе, приём и опрос."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update

from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.modules.platform.infrastructure.postgres.models import UserModel
from backend.modules.wb_screenshot_control.application.extension_service import hash_token
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import (
    ExtensionInstallModel,
    PairingCodeModel,
)
from backend.modules.wb_screenshot_control.presentation.http.schemas import ExtensionMeOut, ExtensionPairOut
from backend.tests.wb_screenshot_control.http_stand import EXTENSION, MANAGER, Stand, open_stand, synthetic_png

CHAT_URL = "https://seller.wildberries.ru/chat-with-clients?chatId=0f8fad5b-d9cb-469f-a165-70867728950e"


def pair_body(code: str, install_id: str = "chrome-install-0001", ext_version: str = "0.1.0") -> dict[str, Any]:
    return {"code": code, "install_id": install_id, "browser": "Chrome 130", "ext_version": ext_version}


def bearer(token: str, version: str = "0.1.0") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Extension-Version": version}


async def new_code(stand: Stand, headers: dict[str, str]) -> str:
    response = await stand.client.post(f"{MANAGER}/extension/pairing-code", headers=headers)
    assert response.status_code == 200, response.text
    code: str = response.json()["code"]
    return code


async def paired(stand: Stand, role: str = "manager", install_id: str = "chrome-install-0001") -> str:
    headers = await stand.sign_in(role)
    response = await stand.client.post(f"{EXTENSION}/pair", json=pair_body(await new_code(stand, headers), install_id))
    assert response.status_code == 200, response.text
    token: str = response.json()["token"]
    return token


async def ext_upload(stand: Stand, token: str, **form: Any) -> Any:
    data = {
        "outcome": "deleted",
        "client_submission_id": str(uuid.uuid4()),
        "client_url": CHAT_URL,
        "client_captured_at": "2026-10-23T09:00:00Z",
        "tz_offset_min": "-180",
        "viewport_w": "1440",
        "viewport_h": "900",
        "dpr": "2",
        **form,
    }
    data = {key: value for key, value in data.items() if value is not None}
    return await stand.client.post(
        f"{EXTENSION}/submissions",
        headers=bearer(token),
        data=data,
        files={"file": ("tab.png", synthetic_png(), "image/png")},
    )


async def test_pairing_gives_a_token_once_and_the_code_burns(stand: Stand) -> None:
    manager = await stand.sign_in("manager")
    first = await new_code(stand, manager)
    second = await new_code(stand, manager)
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{8}", second) and first != second

    stale = await stand.client.post(f"{EXTENSION}/pair", json=pair_body(first))
    assert (stale.status_code, stale.headers["X-Error-Code"]) == (404, "code_invalid")
    response = await stand.client.post(f"{EXTENSION}/pair", json=pair_body(second))
    assert response.status_code == 200, response.text
    answer = ExtensionPairOut.model_validate(response.json())
    assert answer.token.startswith("mas_") and len(answer.token) >= 40
    assert answer.username == f"{stand.prefix}-manager"
    assert answer.allowed_url_prefixes == ["https://seller.wildberries.ru/chat-with-clients"]

    again = await stand.client.post(f"{EXTENSION}/pair", json=pair_body(second, "chrome-install-0002"))
    assert (again.status_code, again.headers["X-Error-Code"]) == (404, "code_invalid")

    me = await stand.client.get(f"{EXTENSION}/me", headers=bearer(answer.token))
    assert me.status_code == 200, me.text
    parsed = ExtensionMeOut.model_validate(me.json())
    assert parsed.username == answer.username and parsed.summary.counts.accepted_total == 0

    async with stand.database.session() as session:
        install = (await session.execute(ExtensionInstallModel.__table__.select())).one()
        assert install.token_hash != answer.token and answer.token not in str(install)
        assert (install.ext_version, install.browser) == ("0.1.0", "Chrome 130")


async def test_expired_code_and_outdated_extension_are_refused(stand: Stand) -> None:
    manager = await stand.sign_in("manager")
    code = await new_code(stand, manager)
    outdated = await stand.client.post(f"{EXTENSION}/pair", json=pair_body(code, ext_version="0.0.9"))
    assert (outdated.status_code, outdated.headers["X-Error-Code"]) == (426, "upgrade_required")

    async with stand.database.session() as session:
        await session.execute(update(PairingCodeModel).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await session.commit()
    expired = await stand.client.post(f"{EXTENSION}/pair", json=pair_body(code))
    assert (expired.status_code, expired.headers["X-Error-Code"]) == (404, "code_invalid")


@pytest.mark.parametrize("code", ["123456", "ABCDEFGHI", "ABCDEFGO", "abcd2345"])
async def test_pairing_codes_are_eight_crockford_characters(stand: Stand, code: str) -> None:
    response = await stand.client.post(f"{EXTENSION}/pair", json=pair_body(code))
    assert response.status_code == 422


async def test_guessing_codes_hits_the_ip_limit(stand: Stand) -> None:
    limit = stand.application.settings.screenshot_control.pair_attempts_per_window
    for _ in range(limit):
        refused = await stand.client.post(f"{EXTENSION}/pair", json=pair_body("ABCD2345"))
        assert (refused.status_code, refused.headers["X-Error-Code"]) == (404, "code_invalid")
    blocked = await stand.client.post(f"{EXTENSION}/pair", json=pair_body("ABCD2345"))
    assert (blocked.status_code, blocked.headers["X-Error-Code"]) == (429, "rate_limited")
    assert int(blocked.headers["Retry-After"]) > 0


async def test_token_checks_on_every_request(stand: Stand) -> None:
    token = await paired(stand)

    unknown = await stand.client.get(f"{EXTENSION}/me", headers=bearer("mas_" + "x" * 43))
    assert (unknown.status_code, unknown.headers["X-Error-Code"]) == (401, "bad_token")
    jwt_like = await stand.client.get(f"{EXTENSION}/me", headers=bearer("eyJhbGciOi.x.y"))
    assert (jwt_like.status_code, jwt_like.headers["X-Error-Code"]) == (401, "bad_token")
    outdated = await stand.client.get(f"{EXTENSION}/me", headers=bearer(token, "0.0.9"))
    assert (outdated.status_code, outdated.headers["X-Error-Code"]) == (426, "upgrade_required")
    no_version = await stand.client.get(f"{EXTENSION}/me", headers={"Authorization": f"Bearer {token}"})
    assert no_version.status_code == 426

    async with stand.database.session() as session:
        await session.execute(update(UserModel).where(UserModel.id == stand.users["manager"]).values(is_active=False))
        await session.commit()
    blocked = await stand.client.get(f"{EXTENSION}/me", headers=bearer(token))
    assert (blocked.status_code, blocked.headers["X-Error-Code"]) == (403, "user_blocked")
    async with stand.database.session() as session:
        await session.execute(update(UserModel).where(UserModel.id == stand.users["manager"]).values(is_active=True))
        await session.execute(update(ExtensionInstallModel).values(last_used_at=datetime.now(UTC) - timedelta(days=61)))
        await session.commit()
    idle = await stand.client.get(f"{EXTENSION}/me", headers=bearer(token))
    assert (idle.status_code, idle.headers["X-Error-Code"]) == (401, "install_revoked")


async def test_installs_are_revoked_by_their_owner_only(stand: Stand) -> None:
    manager = await stand.sign_in("manager")
    other = await stand.sign_in("other")
    first = await paired(stand, install_id="chrome-install-0001")
    repaired = await paired(stand, install_id="chrome-install-0001")
    replaced = await stand.client.get(f"{EXTENSION}/me", headers=bearer(first))
    assert (replaced.status_code, replaced.headers["X-Error-Code"]) == (401, "install_revoked")
    assert (await stand.client.get(f"{EXTENSION}/me", headers=bearer(repaired))).status_code == 200

    installs = (await stand.client.get(f"{MANAGER}/extension/installs", headers=manager)).json()
    assert len(installs) == 1 and installs[0]["last_seen_at"]
    foreign = await stand.client.delete(f"{MANAGER}/extension/installs/{installs[0]['id']}", headers=other)
    assert (foreign.status_code, foreign.headers["X-Error-Code"]) == (404, "not_found")
    assert (await stand.client.get(f"{EXTENSION}/me", headers=bearer(repaired))).status_code == 200

    own = await stand.client.delete(f"{MANAGER}/extension/installs/{installs[0]['id']}", headers=manager)
    assert own.status_code == 204
    revoked = await stand.client.get(f"{EXTENSION}/me", headers=bearer(repaired))
    assert (revoked.status_code, revoked.headers["X-Error-Code"]) == (401, "install_revoked")
    assert (await stand.client.get(f"{MANAGER}/extension/installs", headers=manager)).json() == []


async def test_live_installs_per_manager_are_capped(tmp_path: Path) -> None:
    async with open_stand(tmp_path, max_extension_installs=2, roles=("manager",)) as stand:
        tokens = [await paired(stand, install_id=f"chrome-install-000{index}") for index in range(3)]
        statuses = [(await stand.client.get(f"{EXTENSION}/me", headers=bearer(token))).status_code for token in tokens]
        assert statuses == [401, 200, 200]


async def test_extension_upload_and_polling(stand: Stand) -> None:
    token = await paired(stand)
    other = await stand.sign_in("other")
    foreign = (await stand.upload(other, seed=3)).json()["id"]

    missing = await ext_upload(stand, token, outcome=None)
    assert (missing.status_code, missing.headers["X-Error-Code"]) == (422, "outcome_required")

    client_id = str(uuid.uuid4())
    sent = await ext_upload(stand, token, client_submission_id=client_id)
    assert sent.status_code == 201, sent.text
    assert (sent.json()["channel"], sent.json()["status"], sent.json()["outcome"]) == (
        "extension",
        "processing",
        "deleted",
    )
    repeat = await ext_upload(stand, token, client_submission_id=client_id)
    assert (repeat.status_code, repeat.json()["id"]) == (200, sent.json()["id"])

    row = await stand.submission(sent.json()["id"])
    assert (row.client_url, row.client_tz_offset_min, row.outcome_source) == (CHAT_URL, -180, "button")
    assert row.client_meta | {"user_agent": None} == {
        "user_agent": None,
        "ext_version": "0.1.0",
        "viewport_w": 1440,
        "viewport_h": 900,
        "dpr": 2.0,
    }
    assert row.install_id is not None

    listed = (await stand.client.get(f"{EXTENSION}/submissions", headers=bearer(token))).json()
    assert [item["id"] for item in listed["items"]] == [sent.json()["id"]]
    polled = await stand.client.get(f"{EXTENSION}/submissions/{sent.json()['id']}", headers=bearer(token))
    assert polled.json()["status"] == "processing"
    stranger = await stand.client.get(f"{EXTENSION}/submissions/{foreign}", headers=bearer(token))
    assert (stranger.status_code, stranger.headers["X-Error-Code"]) == (404, "not_found")

    await stand.decide(sent.json()["id"], "accepted")
    summary = await stand.client.get(f"{EXTENSION}/summary", headers=bearer(token))
    assert summary.json()["counts"] == {"positive": 0, "amended": 0, "deleted": 1, "accepted_total": 1}


async def test_extension_is_closed_when_the_module_is_off(tmp_path: Path) -> None:
    async with open_stand(tmp_path, mode="off", roles=("manager",)) as stand:
        token = "mas_" + "y" * 43
        async with stand.database.session() as session:
            session.add(
                ExtensionInstallModel(
                    user_id=stand.users["manager"],
                    install_id="chrome-install-0001",
                    token_hash=hash_token(token),
                    last_used_at=datetime.now(UTC),
                )
            )
            await session.commit()
        bad = await stand.client.get(f"{EXTENSION}/me", headers=bearer("mas_" + "x" * 43))
        assert (bad.status_code, bad.headers["X-Error-Code"]) == (401, "bad_token")
        closed = await stand.client.get(f"{EXTENSION}/me", headers=bearer(token))
        assert (closed.status_code, closed.headers["X-Error-Code"]) == (503, "module_off")


async def test_manager_routes_need_the_service_and_the_extension_token_does_not(tmp_path: Path) -> None:
    async with open_stand(tmp_path, roles=("admin", "manager", "other"), without_service=("other",)) as stand:
        token = await paired(stand)
        async with stand.database.session() as session:
            await UserRepository(session).replace_services(stand.users["manager"], set(), stand.users["admin"])

        for headers in (await stand.sign_in("manager"), await stand.sign_in("other")):
            for method, path in (
                ("GET", f"{MANAGER}/submissions"),
                ("GET", f"{MANAGER}/summary"),
                ("GET", f"{MANAGER}/submissions/{uuid.uuid4()}"),
                ("POST", f"{MANAGER}/extension/pairing-code"),
            ):
                refused = await stand.client.request(method, path, headers=headers)
                assert refused.status_code == 403, (path, refused.text)
                assert refused.headers.get("X-Access-Denied"), path
            denied_upload = await stand.upload(headers)
            assert denied_upload.status_code == 403 and denied_upload.headers.get("X-Access-Denied")

        me = await stand.client.get(f"{EXTENSION}/me", headers=bearer(token))
        assert me.status_code == 200, me.text
        sent = await ext_upload(stand, token)
        assert sent.status_code == 201, sent.text
