"""Подготовка картинок для модели на синтетических скринах (Pillow, никаких реальных данных)."""

import io
import os

import pytest
from PIL import Image, ImageDraw, ImageFont

from backend.modules.wb_screenshot_control.infrastructure.vision import (
    ImageUnreadable,
    address_bar_box,
    address_bar_part,
    full_frame_part,
    open_frame,
)

GUID = "ad70e889-d235-43bf-a634-b60a65bbce9b"
URL_TEXT = f"seller.wildberries.ru/chat-with-clients?chatId={GUID}&chatType=1"


def desktop_screenshot(
    width: int = 1920,
    height: int = 1080,
    *,
    scale: float = 1.0,
    menu_bar: int = 0,
    url: str = URL_TEXT,
    fmt: str = "PNG",
) -> tuple[bytes, tuple[int, int, int, int]]:
    """Окно Chrome с кабинетом WB. Возвращает байты и рамку текста адреса (для проверки вырезки).

    Геометрия как у генератора этапа 0: вкладки 40 px, адресная строка 44 px (при масштабе 1).
    """
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    tabs, bar = round(40 * scale), round(44 * scale)
    top = menu_bar + tabs
    draw.rectangle([0, 0, width, menu_bar], fill="#e8e8e8")
    draw.rectangle([0, menu_bar, width, top], fill="#dee1e6")
    draw.rounded_rectangle([round(110 * scale), top + 8, width - 200, top + bar - 6], 15, fill="#f1f3f4")
    font = ImageFont.load_default(size=round(17 * scale))
    text_box = draw.textbbox((round(140 * scale), top + round(13 * scale)), url, font=font)
    draw.text((round(140 * scale), top + round(13 * scale)), url, fill="#202124", font=font)
    # шапка кабинета, список чатов, переписка и панель «О заказе» с отзывом
    draw.rectangle([0, top + bar, width, top + bar + round(60 * scale)], fill="#a020f0")
    draw.rectangle([0, top + bar + round(60 * scale), round(380 * scale), height], fill="#f6f6f9")
    panel = width - round(450 * scale)
    draw.line([panel, top + bar, panel, height], fill="#e3e3e3", width=2)
    small = ImageFont.load_default(size=round(16 * scale))
    draw.text((panel + 24, round(400 * scale)), "22 октября, 06:39", fill="#888", font=small)
    draw.text((panel + 24, round(446 * scale)), "Размер маломерит", fill="#222", font=small)
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    x0, y0, x1, y1 = (round(value) for value in text_box)
    return buffer.getvalue(), (x0, y0, x1, y1)


def phone_screenshot(width: int = 1170, height: int = 2532) -> bytes:
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 130, width, 290], fill="#f7f7f7")
    draw.rounded_rectangle([60, 340, width - 60, 860], 30, fill="#f3f3f3")
    draw.rounded_rectangle([width - 840, 920, width - 60, 1100], 30, fill="#ececec")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def decoded(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


@pytest.mark.parametrize(
    ("width", "height", "scale", "menu_bar"),
    [
        (1920, 1080, 1.0, 0),  # окно Chrome на весь экран, Windows
        (1440, 810, 0.75, 0),  # тот же скрин, уменьшенный
        (1280, 720, 1.0, 0),  # небольшое окно
        (1280, 800, 1.0, 25),  # полный экран Mac без ретины, со строкой меню
        (2560, 1600, 2.0, 0),  # окно на ретине
        (2880, 1800, 2.0, 74),  # полный экран ретины со строкой меню и «чёлкой»
        (3840, 2160, 1.5, 0),  # 4K с масштабом 150 %
    ],
)
def test_the_address_bar_crop_covers_the_whole_url(width: int, height: int, scale: float, menu_bar: int) -> None:
    _, url_box = desktop_screenshot(width, height, scale=scale, menu_bar=menu_bar)

    left, top, right, bottom = address_bar_box(width, height)

    assert (left, top, right) == (0, 0, width)
    assert bottom >= url_box[3] + 4, "нижний край адреса срезан"
    assert url_box[2] <= right
    # вырезка — полоса сверху, а не пол-экрана
    assert bottom <= max(0.2 * height, 200)


def test_the_crop_is_enlarged_up_to_twice_within_the_side_limit() -> None:
    data, url_box = desktop_screenshot()

    crop = decoded(address_bar_part(open_frame(data)).data)

    _, _, width, height = address_bar_box(1920, 1080)
    assert crop.width == 2800 and crop.height == round(height * 2800 / width)
    small, _ = desktop_screenshot(1280, 720)
    assert decoded(address_bar_part(open_frame(small)).data).size == (2560, 400)


def test_the_crop_keeps_the_url_pixels() -> None:
    data, (x0, y0, x1, y1) = desktop_screenshot()

    crop = decoded(address_bar_part(open_frame(data)).data).convert("L")

    ratio = crop.width / 1920
    region = crop.crop((round(x0 * ratio), round(y0 * ratio), round(x1 * ratio), round(y1 * ratio)))
    darkest = region.getextrema()[0]
    assert isinstance(darkest, int | float) and darkest < 80, "текст адреса не попал в вырезку"


@pytest.mark.parametrize(
    ("size", "expected"),
    [((3840, 2160), (2000, 1125)), ((1170, 2532), (924, 2000)), ((1000, 500), (1000, 500))],
)
def test_the_frame_is_shrunk_to_the_side_limit_and_never_enlarged(
    size: tuple[int, int], expected: tuple[int, int]
) -> None:
    image = Image.new("RGB", size, "#ffffff")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")

    part = full_frame_part(open_frame(buffer.getvalue()), 2000)

    assert part.mime == "image/png" and decoded(part.data).size == expected


def test_a_heavy_frame_is_sent_as_jpeg_to_stay_under_the_body_limit() -> None:
    noise = Image.frombytes("RGB", (1500, 1500), os.urandom(1500 * 1500 * 3))
    buffer = io.BytesIO()
    noise.save(buffer, "PNG")

    part = full_frame_part(open_frame(buffer.getvalue()), 2000)

    assert part.mime == "image/jpeg" and decoded(part.data).format == "JPEG"


def test_transparency_and_jpeg_input_are_accepted() -> None:
    rgba = Image.new("RGBA", (800, 600), (255, 0, 0, 128))
    buffer = io.BytesIO()
    rgba.save(buffer, "PNG")
    jpeg, _ = desktop_screenshot(fmt="JPEG")

    assert open_frame(buffer.getvalue()).image.mode == "RGB"
    assert open_frame(jpeg).size == (1920, 1080)


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\nbroken"])
def test_garbage_is_unreadable(data: bytes) -> None:
    with pytest.raises(ImageUnreadable):
        open_frame(data)
