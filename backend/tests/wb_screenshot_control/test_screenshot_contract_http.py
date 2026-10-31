"""Ручки «Контроля скриншотов ДБС» целиком: авторизация, права, режим off, приём и коды ошибок.

Нужны PostgreSQL и Redis из `deploy/compose.test.yaml`, как у остальных HTTP-тестов.
"""

import re
import uuid
from pathlib import Path
from typing import Any

from backend.app.factory import Application
from backend.modules.wb_screenshot_control.domain import PAIRING_CODE_PATTERN
from backend.modules.wb_screenshot_control.presentation.http.schemas import (
    AdminConfigOut,
    AdminQueuePage,
    ManagerConfigOut,
    ManagersOut,
    PipelineOut,
    SubmissionOut,
)
from backend.tests.wb_screenshot_control.http_stand import (
    ADMIN,
    EXTENSION,
    MANAGER,
    Stand,
    open_stand,
    synthetic_png,
)
from backend.tests.wb_screenshot_control.routes import walk_routes

SAMPLE_ID = "3f2b1c1e-1111-2222-3333-444455556666"


def module_routes(application: Application) -> list[Any]:
    return [route for route in walk_routes(application.get_app().routes) if "screenshot-control" in route.path]


def concrete(path: str) -> str:
    return re.sub(r"\{\w+\}", SAMPLE_ID, path)


async def test_every_route_but_pairing_refuses_an_anonymous_caller(stand: Stand) -> None:
    checked = 0
    for route in module_routes(stand.application):
        if route.path.endswith("/extension/pair"):
            continue
        for method in route.methods:
            response = await stand.client.request(method, concrete(route.path))
            assert response.status_code == 401, (method, route.path, response.text)
            checked += 1
    assert checked >= 26


async def test_admin_routes_refuse_a_manager_and_serve_an_admin(stand: Stand) -> None:
    manager = await stand.sign_in("manager")
    admin = await stand.sign_in("admin")

    forbidden = 0
    for route in module_routes(stand.application):
        if not route.path.startswith(ADMIN):
            continue
        for method in route.methods:
            response = await stand.client.request(method, concrete(route.path), headers=manager)
            assert response.status_code == 403, (method, route.path)
            forbidden += 1
    assert forbidden >= 12

    queue = await stand.client.get(f"{ADMIN}/queue", params={"kind": "duplicates"}, headers=admin)
    assert queue.status_code == 200, queue.text
    assert AdminQueuePage.model_validate(queue.json()).counts.review == 0
    managers = await stand.client.get(f"{ADMIN}/managers", headers=admin)
    assert ManagersOut.model_validate(managers.json()).items == []
    pipeline = PipelineOut.model_validate((await stand.client.get(f"{ADMIN}/pipeline", headers=admin)).json())
    assert pipeline.mode == "shadow" and set(pipeline.storage.objects) >= {"staged", "stored", "failed"}
    config = await stand.client.get(f"{ADMIN}/config", headers=admin)
    parsed = AdminConfigOut.model_validate(config.json())
    assert (parsed.retention_original_days, parsed.retention_thumb_days) == (365, 365)
    assert parsed.telegram_enabled is False
    secret = stand.application.settings.screenshot_control.hmac_key
    assert secret and secret not in config.text and "hmac" not in config.text

    missing = await stand.client.get(f"{ADMIN}/submissions/{SAMPLE_ID}", headers=admin)
    assert (missing.status_code, missing.headers["X-Error-Code"]) == (404, "not_found")
    no_target = await stand.client.post(
        f"{ADMIN}/submissions/{SAMPLE_ID}/resolve", json={"decision": "duplicate_of"}, headers=admin
    )
    assert no_target.status_code == 422
    corrections = await stand.client.post(
        f"{ADMIN}/submissions/{SAMPLE_ID}/resolve",
        json={"decision": "approve", "corrections": [{"field": "chat_id", "value": "x"}]},
        headers=admin,
    )
    assert corrections.status_code == 422


async def test_web_upload_checks_the_file_and_is_idempotent(stand: Stand) -> None:
    manager = await stand.sign_in("manager")
    client_id = str(uuid.uuid4())

    first = await stand.upload(manager, synthetic_png(), outcome="amended", client_id=client_id)
    assert first.status_code == 201, first.text
    accepted = SubmissionOut.model_validate(first.json())
    assert (accepted.channel, accepted.outcome, accepted.status) == ("web", "amended", "processing")
    assert (accepted.reason_code, accepted.fields, accepted.thumb_url) == (None, None, None)
    again = await stand.upload(manager, synthetic_png(), outcome="amended", client_id=client_id)
    assert (again.status_code, again.json()["id"]) == (200, first.json()["id"])
    other_file = await stand.upload(manager, synthetic_png(shade=200), outcome="amended", client_id=client_id)
    assert (other_file.status_code, other_file.headers["X-Error-Code"]) == (409, "idempotency_conflict")

    no_outcome = await stand.upload(manager, synthetic_png(shade=100), outcome=None)
    assert (no_outcome.status_code, no_outcome.json()["status"]) == (201, "awaiting_outcome")

    text = await stand.upload(manager, b"not an image at all")
    assert (text.status_code, text.headers["X-Error-Code"]) == (415, "unsupported_type")
    tiny = await stand.upload(manager, synthetic_png(320, 200))
    assert (tiny.status_code, tiny.headers["X-Error-Code"]) == (422, "too_small")
    broken = await stand.upload(manager, synthetic_png()[:200])
    assert (broken.status_code, broken.headers["X-Error-Code"]) == (422, "bad_image")
    bad_outcome = await stand.upload(manager, synthetic_png(), outcome="good")
    assert bad_outcome.status_code == 422

    row = await stand.submission(first.json()["id"])
    assert (row.user_id, row.outcome_source) == (stand.users["manager"], "form")
    assert "user_agent" in row.client_meta


async def test_upload_limit_and_full_staging(tmp_path: Path) -> None:
    async with open_stand(tmp_path, max_upload_bytes=1024, roles=("manager",)) as stand:
        manager = await stand.sign_in("manager")
        too_big = await stand.upload(manager, synthetic_png())
        assert (too_big.status_code, too_big.headers["X-Error-Code"]) == (413, "file_too_large")
    async with open_stand(tmp_path, staging_max_used_percent=1, roles=("manager",)) as stand:
        manager = await stand.sign_in("manager")
        full = await stand.upload(manager, synthetic_png())
        assert (full.status_code, full.headers["X-Error-Code"]) == (503, "storage_full")


async def test_manager_basics_answer_with_the_contract(stand: Stand) -> None:
    manager = await stand.sign_in("manager")

    config = ManagerConfigOut.model_validate((await stand.client.get(f"{MANAGER}/config", headers=manager)).json())
    assert [(option.value, option.caption) for option in config.outcomes] == [
        ("positive", "полож"),
        ("amended", "доп"),
        ("deleted", "удален"),
    ]
    assert config.extension_url.endswith("/extension/marketplace-auto-screenshots.zip")
    unknown_status = await stand.client.get(f"{MANAGER}/submissions", params={"status": "maybe"}, headers=manager)
    assert unknown_status.status_code == 422
    bad_cursor = await stand.client.get(f"{MANAGER}/submissions", params={"cursor": "%%%"}, headers=manager)
    assert (bad_cursor.status_code, bad_cursor.headers["X-Error-Code"]) == (422, "bad_cursor")

    code = await stand.client.post(f"{MANAGER}/extension/pairing-code", headers=manager)
    assert code.status_code == 200 and re.fullmatch(PAIRING_CODE_PATTERN, code.json()["code"])

    telegram = await stand.client.post(f"{MANAGER}/telegram/link-token", headers=manager)
    assert (telegram.status_code, telegram.headers["X-Error-Code"]) == (503, "telegram_disabled")
    status = await stand.client.get(f"{MANAGER}/telegram", headers=manager)
    assert status.json() == {"enabled": False, "linked": False, "username": None, "linked_at": None}
    assert (await stand.client.delete(f"{MANAGER}/telegram", headers=manager)).status_code == 204


async def test_a_module_off_for_a_missing_hmac_key_says_why_and_the_rest_of_the_api_works(tmp_path: Path) -> None:
    async with open_stand(tmp_path, mode="off", disabled_reason="hmac_key_missing", requested_mode="shadow") as stand:
        manager = await stand.sign_in("manager")
        upload = await stand.upload(manager, synthetic_png())
        assert (upload.status_code, upload.headers.get("X-Error-Code")) == (503, "module_off")
        assert "HMAC" in upload.text
        automations = await stand.client.get("/api/v1/automations", headers=manager)
        assert automations.status_code == 200


async def test_mode_off_closes_manager_and_extension_but_not_admin(tmp_path: Path) -> None:
    async with open_stand(tmp_path, mode="off") as stand:
        manager = await stand.sign_in("manager")
        admin = await stand.sign_in("admin")
        refused = 0
        for route in module_routes(stand.application):
            web_part = (
                not route.path.startswith(EXTENSION + "/") or "/installs" in route.path or "pairing" in route.path
            )
            if not route.path.startswith(MANAGER) or not web_part or route.path.endswith("/config"):
                continue
            for method in route.methods:
                # PATCH и PUT без тела отвечают 422 раньше проверки режима: они проверены ниже с телом.
                if method in ("PATCH", "PUT") or (
                    method == "POST" and not route.path.endswith(("/pairing-code", "/link-token"))
                ):
                    continue
                response = await stand.client.request(method, concrete(route.path), headers=manager)
                assert (response.status_code, response.headers.get("X-Error-Code")) == (503, "module_off"), (
                    method,
                    route.path,
                )
                refused += 1
        assert refused >= 10

        upload = await stand.upload(manager, synthetic_png())
        patch = await stand.client.patch(
            f"{MANAGER}/submissions/{SAMPLE_ID}/outcome", json={"outcome": "positive"}, headers=manager
        )
        pair = await stand.client.post(
            f"{EXTENSION}/pair", json={"code": "ABCD2345", "install_id": "chrome-test-install", "ext_version": "0.1.0"}
        )
        timezone = await stand.client.put(f"{MANAGER}/settings", json={"timezone": "Asia/Omsk"}, headers=manager)
        for response in (upload, patch, pair, timezone):
            assert (response.status_code, response.headers.get("X-Error-Code")) == (503, "module_off")

        config = await stand.client.get(f"{MANAGER}/config", headers=manager)
        assert (config.status_code, config.json()["mode"]) == (200, "off")
        assert (await stand.client.get(f"{ADMIN}/queue", headers=admin)).status_code == 200
