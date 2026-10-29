import io
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image
from pydantic import ValidationError

from backend.modules.wb_screenshot_control.application import (
    Period,
    PeriodError,
    ScreenshotObjectDelete,
    ScreenshotObjectUpload,
    ScreenshotSubmitted,
    preliminary,
    resolve_period,
)
from backend.modules.wb_screenshot_control.domain import object_key, sniff_image_type, version_supported
from backend.modules.wb_screenshot_control.presentation.http.common import read_screenshot
from backend.shared.settings import load_settings

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
SHA = "ab" + "0" * 62


def encoded(kind: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(buffer, format=kind)
    return buffer.getvalue()


@pytest.mark.parametrize(("kind", "mime"), [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")])
def test_the_type_comes_from_the_bytes(kind: str, mime: str) -> None:
    assert sniff_image_type(encoded(kind)[:16]) == mime


@pytest.mark.parametrize("head", [b"GIF89a....", b"%PDF-1.7", b"", b"\x00\x00\x00\x18ftypheic"])
def test_other_files_are_not_screenshots(head: bytes) -> None:
    assert sniff_image_type(head) is None


def test_keys_are_content_addressed_under_the_module_root() -> None:
    assert object_key(SHA, "orig", "image/png") == f"dbs-screenshots/orig/ab/{SHA}.png"
    assert object_key(SHA, "thumb") == f"dbs-screenshots/thumb/ab/{SHA}.jpg"
    with pytest.raises(ValueError):
        object_key("../etc/passwd", "orig", "image/png")


@pytest.mark.parametrize(
    ("version", "supported"),
    [("0.1.0", True), ("0.10.0", True), ("1.0.0", True), ("0.0.9", False), (None, False), ("latest", False)],
)
def test_extension_versions_compare_as_numbers(version: str | None, supported: bool) -> None:
    assert version_supported(version, "0.1.0") is supported


def test_periods_default_to_today_and_stop_at_a_year() -> None:
    today = date(2026, 10, 23)
    assert resolve_period(None, None, today=today) == Period(today, today)
    assert resolve_period(date(2026, 10, 1), None, today=today) == Period(date(2026, 10, 1), today)
    with pytest.raises(PeriodError):
        resolve_period(date(2025, 1, 1), today, today=today)
    with pytest.raises(PeriodError):
        resolve_period(today, date(2026, 10, 1), today=today)
    start, end = Period(today, today).bounds(ZoneInfo("Europe/Moscow"))
    assert start.astimezone(UTC) == datetime(2026, 10, 22, 21, 0, tzinfo=UTC)
    assert (end - start).days == 1


def test_numbers_are_preliminary_in_shadow_and_before_kpi_start() -> None:
    october = Period(date(2026, 10, 1), date(2026, 10, 31))
    assert preliminary("shadow", october, "") is True
    assert preliminary("enforce", october, "") is False
    assert preliminary("enforce", october, "2026-10-15") is True


def test_storage_events_carry_no_bytes_and_stay_inside_staging() -> None:
    upload = ScreenshotObjectUpload(
        file_id=uuid.uuid4(),
        kind="orig",
        bucket="dbs-screens",
        key=object_key(SHA, "orig", "image/png"),
        staging_path=f"ab/{SHA}.png",
        sha256=SHA,
        content_type="image/png",
        size_bytes=1024,
    )
    payload = upload.payload()
    assert "event_id" not in payload and not any("base64" in name or name == "content" for name in payload)

    for staging_path in ("/etc/passwd", "../outside.png", "ab/../../x.png"):
        with pytest.raises(ValidationError):
            ScreenshotObjectUpload.model_validate({**payload, "staging_path": staging_path})
    with pytest.raises(ValidationError):
        ScreenshotObjectDelete(
            file_id=uuid.uuid4(), kind="orig", bucket="dbs-screens", key="other/key.png", reason="retention"
        )
    with pytest.raises(ValidationError):
        ScreenshotSubmitted.model_validate(
            {
                "submission_id": str(uuid.uuid4()),
                "file_id": str(uuid.uuid4()),
                "sha256": SHA,
                "channel": "web",
                "received_at": "2026-10-23T09:00:00Z",
                "content_base64": "AAAA",
            }
        )


async def test_uploads_are_checked_by_size_and_content() -> None:
    limited = replace(SETTINGS, screenshot_control=replace(SETTINGS.screenshot_control, max_upload_bytes=64))

    png = encoded("PNG")
    data, mime = await read_screenshot(UploadFile(io.BytesIO(png)), SETTINGS)
    assert (data, mime) == (png, "image/png")
    with pytest.raises(HTTPException) as too_big:
        await read_screenshot(UploadFile(io.BytesIO(png + b"\x00" * 64)), limited)
    assert (too_big.value.status_code, too_big.value.headers) == (413, {"X-Error-Code": "file_too_large"})
    with pytest.raises(HTTPException) as wrong:
        await read_screenshot(UploadFile(io.BytesIO(b"hello")), SETTINGS)
    assert wrong.value.status_code == 415
