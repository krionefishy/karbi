"""Каркас ручек «Контроля скриншотов ДБС»: авторизация, коды ошибок и форма ответов.

Нужны PostgreSQL и Redis из `deploy/compose.test.yaml` (как у остальных HTTP-тестов): вход,
флаг администратора и лимит на обмен кода живут там.
"""

import io
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import delete

from backend.app.factory import Application
from backend.modules.platform.application import PasswordService
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.modules.platform.infrastructure.postgres.models import UserModel
from backend.modules.wb_screenshot_control.domain import PAIRING_CODE_PATTERN
from backend.modules.wb_screenshot_control.presentation.http.schemas import (
    AdminConfigOut,
    AdminQueuePage,
    ExtensionMeOut,
    ManagerConfigOut,
    ManagersOut,
    PipelineOut,
    SubmissionOut,
    SubmissionPage,
    Summary,
)
from backend.shared.settings import load_settings
from backend.tests.wb_screenshot_control.routes import walk_routes

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
API = "/api/v1"
MANAGER = f"{API}/wb/screenshot-control"
EXTENSION = f"{MANAGER}/extension"
ADMIN = f"{API}/admin/screenshot-control"
PASSWORD = "correct horse battery staple"
EXTENSION_HEADERS = {"Authorization": "Bearer mas_" + "x" * 43, "X-Extension-Version": "0.1.0"}
SAMPLE_ID = "3f2b1c1e-1111-2222-3333-444455556666"


@dataclass(frozen=True, slots=True)
class Stand:
    client: AsyncClient
    application: Application
    prefix: str


@pytest_asyncio.fixture
async def stand() -> AsyncIterator[Stand]:
    application = Application(SETTINGS)
    app = application.get_app()
    prefix = f"sc-test-{uuid.uuid4().hex[:8]}"
    async with app.router.lifespan_context(app):
        passwords = PasswordService()
        async with application.database.session() as session:
            users = UserRepository(session)
            await users.create(f"{prefix}-admin", passwords.hash(PASSWORD), is_admin=True)
            await users.create(f"{prefix}-manager", passwords.hash(PASSWORD))
        if stale := await application.redis.scan_iter("rate_limit:sc-pair:*"):
            await application.redis.delete(*stale)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            try:
                yield Stand(client, application, prefix)
            finally:
                async with application.database.session() as session:
                    await session.execute(delete(UserModel).where(UserModel.username.startswith(prefix)))
                    await session.commit()


async def sign_in(stand: Stand, role: str) -> dict[str, str]:
    response = await stand.client.post(
        f"{API}/auth/login", json={"username": f"{stand.prefix}-{role}", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def module_routes(application: Application) -> list[Any]:
    return [route for route in walk_routes(application.get_app().routes) if "screenshot-control" in route.path]


def concrete(path: str) -> str:
    return re.sub(r"\{\w+\}", SAMPLE_ID, path)


def synthetic_png(width: int = 640, height: int = 480) -> bytes:
    """Синтетическая картинка: реальных скриншотов в репозитории нет и быть не должно."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (240, 240, 245)).save(buffer, format="PNG")
    return buffer.getvalue()


async def test_every_route_but_pairing_refuses_an_anonymous_caller(stand: Stand) -> None:
    checked = 0
    for route in module_routes(stand.application):
        if route.path.endswith("/extension/pair"):
            continue
        for method in route.methods:
            response = await stand.client.request(method, concrete(route.path))
            assert response.status_code == 401, (method, route.path, response.text)
            checked += 1
    assert checked >= 25


async def test_admin_routes_refuse_a_manager_and_serve_an_admin(stand: Stand) -> None:
    manager = await sign_in(stand, "manager")
    admin = await sign_in(stand, "admin")

    forbidden = 0
    for route in module_routes(stand.application):
        if not route.path.startswith(ADMIN):
            continue
        for method in route.methods:
            response = await stand.client.request(method, concrete(route.path), headers=manager)
            assert response.status_code == 403, (method, route.path)
            forbidden += 1
    assert forbidden >= 11

    queue = await stand.client.get(f"{ADMIN}/queue", params={"kind": "duplicates"}, headers=admin)
    assert queue.status_code == 200, queue.text
    assert AdminQueuePage.model_validate(queue.json()).counts.review == 0
    managers = await stand.client.get(f"{ADMIN}/managers", headers=admin)
    assert ManagersOut.model_validate(managers.json()).items == []
    pipeline = PipelineOut.model_validate((await stand.client.get(f"{ADMIN}/pipeline", headers=admin)).json())
    assert pipeline.mode == "shadow" and set(pipeline.storage.objects) >= {"staged", "stored", "failed"}
    config = AdminConfigOut.model_validate((await stand.client.get(f"{ADMIN}/config", headers=admin)).json())
    assert (config.retention_original_days, config.retention_thumb_days) == (365, 365)
    assert config.telegram_enabled is False

    missing = await stand.client.get(f"{ADMIN}/submissions/{SAMPLE_ID}", headers=admin)
    assert (missing.status_code, missing.headers["X-Error-Code"]) == (404, "not_found")
    no_note = await stand.client.post(
        f"{ADMIN}/submissions/{SAMPLE_ID}/resolve", json={"decision": "decline", "note": " "}, headers=admin
    )
    assert (no_note.status_code, no_note.headers["X-Error-Code"]) == (422, "note_required")
    no_target = await stand.client.post(
        f"{ADMIN}/submissions/{SAMPLE_ID}/resolve", json={"decision": "duplicate_of"}, headers=admin
    )
    assert no_target.status_code == 422


async def test_manager_routes_answer_with_the_contract(stand: Stand) -> None:
    manager = await sign_in(stand, "manager")

    summary = Summary.model_validate((await stand.client.get(f"{MANAGER}/summary", headers=manager)).json())
    assert summary.mode == "shadow" and summary.preliminary is True
    assert summary.period.from_ == summary.period.to

    too_long = await stand.client.get(
        f"{MANAGER}/summary", params={"from": "2025-01-01", "to": "2026-10-23"}, headers=manager
    )
    assert (too_long.status_code, too_long.headers["X-Error-Code"]) == (422, "period_too_long")

    page = await stand.client.get(f"{MANAGER}/submissions", params={"status": "needs_review"}, headers=manager)
    assert SubmissionPage.model_validate(page.json()).items == []
    unknown_status = await stand.client.get(f"{MANAGER}/submissions", params={"status": "maybe"}, headers=manager)
    assert unknown_status.status_code == 422

    config = ManagerConfigOut.model_validate((await stand.client.get(f"{MANAGER}/config", headers=manager)).json())
    assert [(option.value, option.caption) for option in config.outcomes] == [
        ("positive", "полож"),
        ("amended", "доп"),
        ("deleted", "удален"),
    ]
    assert config.extension_url.endswith("/extension/marketplace-auto-screenshots.zip")

    missing = await stand.client.get(f"{MANAGER}/submissions/{SAMPLE_ID}", headers=manager)
    assert (missing.status_code, missing.headers["X-Error-Code"]) == (404, "not_found")

    code = await stand.client.post(f"{MANAGER}/extension/pairing-code", headers=manager)
    assert code.status_code == 200 and re.fullmatch(PAIRING_CODE_PATTERN, code.json()["code"])

    telegram = await stand.client.post(f"{MANAGER}/telegram/link-token", headers=manager)
    assert (telegram.status_code, telegram.headers["X-Error-Code"]) == (503, "telegram_disabled")


async def test_web_upload_checks_the_file_and_is_idempotent(stand: Stand) -> None:
    manager = await sign_in(stand, "manager")
    client_id = str(uuid.uuid4())
    form = {"client_submission_id": client_id, "outcome": "amended"}
    files = {"file": ("shot.png", synthetic_png(), "image/png")}

    first = await stand.client.post(f"{MANAGER}/submissions", data=form, files=files, headers=manager)
    assert first.status_code == 201, first.text
    accepted = SubmissionOut.model_validate(first.json())
    assert (accepted.channel, accepted.outcome, accepted.status) == ("web", "amended", "processing")
    again = await stand.client.post(f"{MANAGER}/submissions", data=form, files=files, headers=manager)
    assert again.json()["id"] == first.json()["id"]

    no_outcome = await stand.client.post(
        f"{MANAGER}/submissions", data={"client_submission_id": str(uuid.uuid4())}, files=files, headers=manager
    )
    assert no_outcome.json()["status"] == "awaiting_outcome"

    text = {"file": ("shot.png", b"not an image at all", "image/png")}
    refused = await stand.client.post(f"{MANAGER}/submissions", data=form, files=text, headers=manager)
    assert (refused.status_code, refused.headers["X-Error-Code"]) == (415, "unsupported_type")

    bad_outcome = await stand.client.post(
        f"{MANAGER}/submissions", data={**form, "outcome": "good"}, files=files, headers=manager
    )
    assert bad_outcome.status_code == 422


async def test_extension_token_version_and_pairing_limit(stand: Stand) -> None:
    me = await stand.client.get(f"{EXTENSION}/me", headers=EXTENSION_HEADERS)
    assert me.status_code == 200, me.text
    assert ExtensionMeOut.model_validate(me.json()).allowed_url_prefixes == [
        "https://seller.wildberries.ru/chat-with-clients"
    ]

    jwt_like = await stand.client.get(f"{EXTENSION}/me", headers={"Authorization": "Bearer eyJhbGciOi.x.y"})
    assert (jwt_like.status_code, jwt_like.headers["X-Error-Code"]) == (401, "bad_token")
    outdated = await stand.client.get(f"{EXTENSION}/me", headers={**EXTENSION_HEADERS, "X-Extension-Version": "0.0.9"})
    assert (outdated.status_code, outdated.headers["X-Error-Code"]) == (426, "upgrade_required")

    upload = await stand.client.post(
        f"{EXTENSION}/submissions",
        headers=EXTENSION_HEADERS,
        data={
            "outcome": "deleted",
            "client_submission_id": str(uuid.uuid4()),
            "client_url": "https://seller.wildberries.ru/chat-with-clients?chatId=00000000-0000-0000-0000-000000000000",
            "client_captured_at": "2026-10-23T09:00:00Z",
            "tz_offset_min": "-180",
        },
        files={"file": ("tab.png", synthetic_png(), "image/png")},
    )
    assert upload.status_code == 201, upload.text
    assert upload.json()["channel"] == "extension"

    attempt = {"code": "ABCD2345", "install_id": "chrome-test-install", "browser": "Chrome", "ext_version": "0.1.0"}
    lowercase = await stand.client.post(f"{EXTENSION}/pair", json={**attempt, "code": "abcd2345"})
    assert lowercase.status_code == 422
    limit = SETTINGS.screenshot_control.pair_attempts_per_window
    for _ in range(limit - 1):
        refused = await stand.client.post(f"{EXTENSION}/pair", json=attempt)
        assert (refused.status_code, refused.headers["X-Error-Code"]) == (404, "code_invalid")
    blocked = await stand.client.post(f"{EXTENSION}/pair", json=attempt)
    assert (blocked.status_code, blocked.headers["X-Error-Code"]) == (429, "rate_limited")
    assert int(blocked.headers["Retry-After"]) > 0


@pytest.mark.parametrize("code", ["123456", "ABCDEFGHI", "ABCDEFGO"])
async def test_pairing_codes_are_eight_crockford_characters(stand: Stand, code: str) -> None:
    attempt = {"code": code, "install_id": "chrome-test-install", "ext_version": "0.1.0"}
    response = await stand.client.post(f"{EXTENSION}/pair", json=attempt)
    assert response.status_code == 422
