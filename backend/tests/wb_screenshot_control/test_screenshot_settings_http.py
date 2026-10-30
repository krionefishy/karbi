"""Пояс менеджера на живой базе: ручки `/settings`, доступ к сервису, `/extension/me`, таблица `user_settings`."""

from pathlib import Path

from sqlalchemy import select

from backend.modules.wb_screenshot_control.infrastructure.postgres.models import UserSettingsModel
from backend.modules.wb_screenshot_control.presentation.http.schemas import ManagerSettingsOut
from backend.tests.wb_screenshot_control.http_stand import EXTENSION, MANAGER, Stand, open_stand
from backend.tests.wb_screenshot_control.test_screenshot_extension_http import bearer, paired

SETTINGS = f"{MANAGER}/settings"


async def test_the_default_is_moscow_and_the_manager_changes_only_his_own(stand: Stand) -> None:
    manager = await stand.sign_in("manager")
    other = await stand.sign_in("other")

    first = await stand.client.get(SETTINGS, headers=manager)
    assert first.status_code == 200, first.text
    parsed = ManagerSettingsOut.model_validate(first.json())
    assert (parsed.timezone, parsed.default) == ("Europe/Moscow", "Europe/Moscow")
    labels = {zone.id: zone.label for zone in parsed.zones}
    assert len(labels) == 13 and labels["Europe/Moscow"] == "Москва, UTC+3"
    assert labels["Asia/Vladivostok"] == "Владивосток, UTC+10"
    assert next(zone for zone in parsed.zones if zone.id == "Asia/Omsk").utc_offset_minutes == 360

    changed = await stand.client.put(SETTINGS, json={"timezone": "Asia/Yekaterinburg"}, headers=manager)
    assert changed.status_code == 200, changed.text
    assert changed.json()["timezone"] == "Asia/Yekaterinburg" and changed.json()["zones"] == first.json()["zones"]
    assert (await stand.client.get(SETTINGS, headers=manager)).json()["timezone"] == "Asia/Yekaterinburg"
    assert (await stand.client.get(SETTINGS, headers=other)).json()["timezone"] == "Europe/Moscow"

    again = await stand.client.put(SETTINGS, json={"timezone": "Asia/Vladivostok"}, headers=manager)
    assert again.json()["timezone"] == "Asia/Vladivostok"
    async with stand.database.session() as session:
        rows = list(await session.scalars(select(UserSettingsModel)))
    assert [(row.user_id, row.timezone) for row in rows] == [(stand.users["manager"], "Asia/Vladivostok")]
    assert rows[0].updated_at is not None


async def test_an_unknown_timezone_is_refused(stand: Stand) -> None:
    manager = await stand.sign_in("manager")
    for value in ("Europe/Kyiv", "Moscow", "UTC", "Europe/Moscow "):
        refused = await stand.client.put(SETTINGS, json={"timezone": value}, headers=manager)
        assert (refused.status_code, refused.headers.get("X-Error-Code")) == (422, "bad_timezone"), value
        assert refused.json()["detail"] == "Такого часового пояса нет в списке"
    extra = await stand.client.put(SETTINGS, json={"timezone": "Asia/Omsk", "user_id": "x"}, headers=manager)
    assert extra.status_code == 422
    assert (await stand.client.get(SETTINGS, headers=manager)).json()["timezone"] == "Europe/Moscow"


async def test_settings_need_the_service(tmp_path: Path) -> None:
    async with open_stand(tmp_path, roles=("admin", "manager", "other"), without_service=("other",)) as stand:
        other = await stand.sign_in("other")
        for method, body in (("GET", None), ("PUT", {"timezone": "Asia/Omsk"})):
            refused = await stand.client.request(method, SETTINGS, json=body, headers=other)
            assert refused.status_code == 403 and refused.headers.get("X-Access-Denied"), (method, refused.text)
        anonymous = await stand.client.get(SETTINGS)
        assert anonymous.status_code == 401


async def test_the_extension_sees_the_managers_timezone(stand: Stand) -> None:
    token = await paired(stand)
    me = await stand.client.get(f"{EXTENSION}/me", headers=bearer(token))
    assert me.json()["timezone"] == "Europe/Moscow"
    manager = await stand.sign_in("manager")
    await stand.client.put(SETTINGS, json={"timezone": "Asia/Kamchatka"}, headers=manager)
    me = await stand.client.get(f"{EXTENSION}/me", headers=bearer(token))
    assert me.json()["timezone"] == "Asia/Kamchatka"
