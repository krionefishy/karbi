"""Синтетические скриншоты для стенда: окно Chrome с кабинетом WB и чат WB Partners в телефоне.

Картинки рисуются Pillow и в репозиторий не попадают (deploy/stand/samples/ в .gitignore): в них
вымышленные покупатели и чаты, но репозиторий публичный, а набор пересобирается одной командой.

На каждом скрине в левом нижнем углу — метка из квадратов: по ней поддельная модель (fake_vlm.py) отдаёт
ровно те поля, что нарисованы на картинке. Даты относительные (вчера и раньше), поэтому набор стоит
пересобирать в день показа: `just stand-samples`.

    uv run python deploy/stand/make_samples.py [--out deploy/stand/samples] [--random N]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_vlm import MARK_BLOCK, MARK_SYNC, MARK_X, encode_mark, facts, mark_y  # noqa: E402

FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)
BOLD_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
)

PURPLE = (203, 17, 171)
INK = (32, 32, 40)
MUTED = (128, 128, 140)
LINE = (226, 226, 232)
STAR_ON = (255, 168, 0)
STAR_OFF = (220, 220, 226)


def _font_path(candidates: tuple[str, ...]) -> str:
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    raise SystemExit("Нет TTF-шрифта с кириллицей (Arial или DejaVu): поставьте fonts-dejavu или запустите на macOS")


REGULAR = _font_path(FONT_CANDIDATES)
BOLD = _font_path(BOLD_CANDIDATES) if any(Path(c).exists() for c in BOLD_CANDIDATES) else REGULAR


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(BOLD if bold else REGULAR, size)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        probe = f"{line} {word}".strip()
        if draw.textlength(probe, font=fnt) <= width:
            line = probe
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def draw_mark(image: Image.Image, text_seed: int, chat_seed: int, mode: str) -> None:
    draw = ImageDraw.Draw(image)
    y = mark_y(image.height)
    bits = [*MARK_SYNC, *encode_mark(text_seed, chat_seed, mode)]
    draw.rectangle(
        (0, y - MARK_BLOCK, MARK_X + len(bits) * MARK_BLOCK + MARK_BLOCK, image.height), fill=(255, 255, 255)
    )
    for index, bit in enumerate(bits):
        x = MARK_X + index * MARK_BLOCK
        draw.rectangle((x, y, x + MARK_BLOCK - 1, y + MARK_BLOCK - 1), fill=(255, 255, 255) if bit else (0, 0, 0))


def stars(draw: ImageDraw.ImageDraw, x: int, y: int, count: int, size: int) -> None:
    for index in range(5):
        cx = x + index * (size + 6) + size // 2
        cy = y + size // 2
        r, r2 = size / 2, size / 5
        points = []
        for k in range(10):
            angle = -math.pi / 2 + k * math.pi / 5
            radius = r if k % 2 == 0 else r2
            points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        draw.polygon(points, fill=STAR_ON if index < count else STAR_OFF)


DESKTOP_SIZES = ((1920, 1080), (1680, 1048), (1440, 896), (1600, 1000), (1856, 1040))
MOBILE_SIZES = ((904, 1952), (864, 1872), (752, 1624))
OLDER_CLIENT = (
    "Здравствуйте, заказ пришёл, но есть вопрос",
    "Подскажите, как оформить возврат?",
    "Спасибо, посмотрю и отпишусь",
    "Можно ли обменять на другой размер?",
)


def photo(d: ImageDraw.ImageDraw, box: tuple[int, int, int, int], seed: int) -> None:
    """«Фото товара»: крупные цветные пятна. Без них кадры одного макета почти совпадают по dHash
    (9×8 серых пикселей), и разные кейсы система честно считает похожей картинкой с другими полями."""
    rng = random.Random(seed)
    x0, y0, x1, y1 = box
    d.rectangle(box, fill=tuple(rng.randrange(40, 230) for _ in range(3)))
    for _ in range(4):
        w, h = rng.randrange((x1 - x0) // 4, x1 - x0), rng.randrange((y1 - y0) // 4, y1 - y0)
        x, y = rng.randrange(x0, x1 - w // 2), rng.randrange(y0, y1 - h // 2)
        d.ellipse((x, y, min(x + w, x1), min(y + h, y1)), fill=tuple(rng.randrange(0, 255) for _ in range(3)))


def desktop(f: dict[str, Any]) -> Image.Image:
    seed = f["text_seed"] * 7919 + f["chat_seed"]
    rng = random.Random(seed)
    width, height = DESKTOP_SIZES[seed % len(DESKTOP_SIZES)]
    image = Image.new("RGB", (width, height), (246, 246, 250))
    d = ImageDraw.Draw(image)
    side_w = rng.choice((340, 380, 420))
    panel_w = rng.choice((440, 480, 520))
    px = width - panel_w
    # Окно Chrome: полоса вкладок, адресная строка.
    d.rectangle((0, 0, width, 40), fill=(222, 225, 230))
    d.rounded_rectangle((90, 6, 420, 40), radius=10, fill=(255, 255, 255))
    d.text((112, 13), "Чаты с покупателями — WB Partners", fill=INK, font=font(14))
    for i, color in enumerate(((255, 95, 87), (254, 188, 46), (40, 200, 64))):
        d.ellipse((16 + i * 22, 14, 28 + i * 22, 26), fill=color)
    d.rectangle((0, 40, width, 84), fill=(255, 255, 255))
    d.text((24, 50), "←  →", fill=MUTED, font=font(18))
    d.rounded_rectangle((110, 48, width - 300, 78), radius=15, fill=(241, 243, 244))
    d.text((130, 53), f["url"].removeprefix("https://"), fill=INK, font=font(17))
    d.line((0, 84, width, 84), fill=LINE)
    # Шапка кабинета.
    d.rectangle((0, 85, width, 145), fill=(255, 255, 255))
    d.text((32, 100), "WB Partners", fill=PURPLE, font=font(26, bold=True))
    d.text(
        (300, 106),
        "Товары   Цены и скидки   Аналитика   Отзывы и вопросы   Чаты с покупателями",
        fill=INK,
        font=font(17),
    )
    d.line((0, 145, width, 145), fill=LINE)
    # Список чатов.
    d.rectangle((0, 146, side_w, height), fill=(255, 255, 255))
    d.line((side_w, 146, side_w, height), fill=LINE)
    d.text((24, 166), "Чаты", fill=INK, font=font(22, bold=True))
    active_row = rng.randrange(0, 5)
    for row in range((height - 216) // 92):
        y = 216 + row * 92
        active = row == active_row
        if active:
            d.rectangle((0, y - 8, side_w - 1, y + 76), fill=(247, 236, 252))
        photo(d, (24, y + 4, 72, y + 52), seed * 31 + row)
        name = f["buyer"] if active else ("Покупатель", "Клиент", "Покупательница")[(row + seed) % 3]
        d.text((88, y + 4), name, fill=INK, font=font(17, bold=active))
        preview = f["follow_up"][:26] + "…" if active else "Здравствуйте, подскажите…"
        d.text((88, y + 32), preview, fill=MUTED, font=font(15))
    # Переписка.
    left, right = side_w + 20, px - 20
    d.text((left + 10, 166), f["buyer"], fill=INK, font=font(22, bold=True))
    d.text((left + 10, 198), "Чат по отзыву", fill=MUTED, font=font(15))
    d.line((side_w, 236, px, 236), fill=LINE)
    y = 270
    d.text(((left + right) // 2 - 60, y), f["date_text"], fill=MUTED, font=font(15))
    y += 44
    older = [
        ("client", OLDER_CLIENT[(seed + i) % len(OLDER_CLIENT)], f["time_text"]) for i in range(rng.randrange(0, 3))
    ]
    bubble_w = min(560, right - left - 60)
    for side, text, at in (
        *older,
        ("seller", f["seller_reply"], f["reply_time"]),
        ("client", f["follow_up"], f["follow_time"]),
    ):
        lines = wrap(d, text, font(17), bubble_w - 40)
        box_h = 24 * len(lines) + 46
        x0 = right - bubble_w if side == "seller" else left + 20
        d.rounded_rectangle(
            (x0, y, x0 + bubble_w, y + box_h), radius=16, fill=(236, 220, 248) if side == "seller" else (255, 255, 255)
        )
        for i, line in enumerate(lines):
            d.text((x0 + 20, y + 14 + i * 24), line, fill=INK, font=font(17))
        d.text((x0 + bubble_w - 60, y + box_h - 26), at, fill=MUTED, font=font(13))
        y += box_h + 24
    d.rounded_rectangle((left + 10, height - 110, right, height - 50), radius=14, fill=(255, 255, 255), outline=LINE)
    d.text((left + 30, height - 92), "Напишите сообщение…", fill=MUTED, font=font(17))
    # Панель «О заказе» с отзывом.
    d.rectangle((px, 146, width, height), fill=(255, 255, 255))
    d.line((px, 146, px, height), fill=LINE)
    d.text((px + 28, 166), "О заказе", fill=INK, font=font(22, bold=True))
    photo(d, (px + 28, 214, px + 188, 414), seed)
    for i, line in enumerate(wrap(d, f["product"], font(17, bold=True), panel_w - 240)):
        d.text((px + 208, 216 + i * 24), line, fill=INK, font=font(17, bold=True))
    d.text((px + 208, 300), f"Артикул WB: {f['article']}", fill=MUTED, font=font(15))
    d.line((px + 28, 440, width - 28, 440), fill=LINE)
    d.text((px + 28, 460), "Отзыв", fill=INK, font=font(19, bold=True))
    stars(d, px + 28, 500, f["stars"], 22)
    d.text((px + 200, 502), f"{f['date_text']}, {f['time_text']}", fill=MUTED, font=font(15))
    for i, line in enumerate(wrap(d, f["text"], font(17), panel_w - 60)):
        d.text((px + 28, 546 + i * 26), line, fill=INK, font=font(17))
    return image


def mobile(f: dict[str, Any]) -> Image.Image:
    seed = f["text_seed"] * 7919 + f["chat_seed"]
    rng = random.Random(seed)
    width, height = MOBILE_SIZES[seed % len(MOBILE_SIZES)]
    image = Image.new("RGB", (width, height), (240, 240, 245))
    d = ImageDraw.Draw(image)
    # Обои чата своего цвета: экраны одного приложения иначе почти совпадают по dHash всего кадра.
    top, bottom = (tuple(rng.randrange(60, 250) for _ in range(3)) for _ in range(2))
    for y in range(230, height - 170):
        t = (y - 230) / (height - 400)
        d.line((0, y, width, y), fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)))
    d.text((48, 26), f["follow_time"], fill=INK, font=font(30, bold=True))
    d.text((width - 170, 26), "LTE  87%", fill=INK, font=font(28))
    d.rectangle((0, 90, width, 230), fill=(255, 255, 255))
    d.text((36, 128), "‹", fill=PURPLE, font=font(60))
    photo(d, (96, 116, 176, 196), seed)
    d.text((196, 120), f["buyer"], fill=INK, font=font(34, bold=True))
    d.text((196, 166), "WB Partners · чат по отзыву", fill=MUTED, font=font(24))
    d.line((0, 230, width, 230), fill=LINE)
    y = 270
    label = f["date_text"]
    tw = d.textlength(label, font=font(24))
    d.rounded_rectangle(((width - tw) / 2 - 20, y, (width + tw) / 2 + 20, y + 44), radius=22, fill=(226, 226, 234))
    d.text(((width - tw) / 2, y + 8), label, fill=MUTED, font=font(24))
    y += 80
    # Карточка товара в чате: крупное «фото», у каждого кейса своё.
    card_h = rng.choice((220, 300, 380))
    d.rounded_rectangle((36, y, width - 36, y + card_h), radius=24, fill=(255, 255, 255))
    photo(d, (60, y + 20, 60 + card_h - 40, y + card_h - 20), seed + 1)
    d.text((card_h + 50, y + 30), f["product"][:18], fill=INK, font=font(28, bold=True))
    d.text((card_h + 50, y + 74), f"Арт. {f['article']}", fill=MUTED, font=font(24))
    y += card_h + 30
    for side, text, at in (
        ("client", f["text"], f["time_text"]),
        ("seller", f["seller_reply"], f["reply_time"]),
        ("client", f["follow_up"], f["follow_time"]),
    ):
        bubble_w = width - 290
        lines = wrap(d, text, font(30), bubble_w - 54)
        box_h = 40 * len(lines) + 70
        x0 = width - bubble_w - 36 if side == "seller" else 36
        d.rounded_rectangle(
            (x0, y, x0 + bubble_w, y + box_h), radius=28, fill=(236, 220, 248) if side == "seller" else (255, 255, 255)
        )
        for i, line in enumerate(lines):
            d.text((x0 + 28, y + 20 + i * 40), line, fill=INK, font=font(30))
        d.text((x0 + bubble_w - 84, y + box_h - 38), at, fill=MUTED, font=font(22))
        y += box_h + 30
    d.rectangle((0, height - 170, width, height), fill=(255, 255, 255))
    d.rounded_rectangle((36, height - 150, width - 36, height - 70), radius=40, fill=(240, 240, 245))
    d.text((80, height - 128), "Сообщение", fill=MUTED, font=font(30))
    return image


# Набор: имя, семя текста/времени, семя чата, макет, режим фейка, назначение.
SAMPLES: tuple[tuple[str, int, int, str, str, str], ...] = (
    ("01-desktop-unique", 11, 11, "desktop", "normal", "Уникальный десктоп: accepted"),
    ("02-desktop-unique", 12, 12, "desktop", "normal", "Уникальный десктоп: accepted"),
    ("03-mobile-unique", 13, 13, "mobile", "normal", "Уникальный чат в телефоне: accepted"),
    ("06-other-chat-same-text", 11, 211, "desktop", "normal", "Текст и время как у 01, другой chatId: needs_review"),
    ("07-desktop-no-outcome", 14, 14, "desktop", "normal", "Загрузить без исхода: ожидание, через сутки — отказ"),
    ("08-recognition-error", 15, 15, "desktop", "fail", "Модель отвечает 500: 4 попытки ~6 мин, затем needs_review"),
    ("09-looks-edited", 16, 16, "desktop", "edited", "Модель видит следы правки: needs_review"),
    ("10-desktop-unique", 17, 17, "desktop", "normal", "Уникальный десктоп для второго менеджера"),
    ("11-mobile-unique", 18, 18, "mobile", "normal", "Уникальный телефон для второго менеджера"),
    ("12-desktop-unique", 19, 19, "desktop", "normal", "Уникальный десктоп"),
    ("13-desktop-unique", 20, 20, "desktop", "normal", "Уникальный десктоп"),
    ("14-mobile-unique", 21, 21, "mobile", "normal", "Уникальный телефон"),
)


SCREEN = (1920, 1080)


def on_screen(window: Image.Image, seed: int) -> Image.Image:
    """Скрин всего экрана: окно браузера не во весь экран, вокруг обои своего цвета. Так и снимают чаще
    всего, и только так кадры разных кейсов расходятся по dHash всего кадра (9×8) дальше строгого порога."""
    rng = random.Random(seed)
    screen = Image.new("RGB", SCREEN)
    d = ImageDraw.Draw(screen)
    top, bottom = (tuple(rng.randrange(0, 256) for _ in range(3)) for _ in range(2))
    for y in range(SCREEN[1]):
        t = y / SCREEN[1]
        d.line((0, y, SCREEN[0], y), fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)))
    for _ in range(3):
        x, y, r = rng.randrange(0, SCREEN[0]), rng.randrange(0, SCREEN[1]), rng.randrange(150, 450)
        d.ellipse((x - r, y - r, x + r, y + r), fill=tuple(rng.randrange(0, 256) for _ in range(3)))
    d.rectangle((0, 0, SCREEN[0], 28), fill=(236, 236, 240))
    d.text((20, 5), "Chrome   Файл   Правка   Вид   История   Закладки", fill=INK, font=font(15))
    scale = rng.choice((0.62, 0.7, 0.78, 0.86))
    shrunk = window.resize((round(window.width * scale), round(window.height * scale)), Image.Resampling.LANCZOS)
    x = rng.randrange(0, SCREEN[0] - shrunk.width + 1)
    screen.paste(shrunk, (x, 28 + rng.randrange(0, 40)))
    return screen


def render(text_seed: int, chat_seed: int, layout: str, mode: str) -> Image.Image:
    f = facts(text_seed, chat_seed)
    seed = text_seed * 7919 + chat_seed
    image = on_screen(desktop(f), seed) if layout == "desktop" else mobile(f)
    draw_mark(image, text_seed, chat_seed, mode)
    return image


def main() -> None:
    parser = argparse.ArgumentParser(description="Синтетические скриншоты для стенда")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "samples"))
    parser.add_argument("--random", type=int, default=0, help="ещё N уникальных скринов со случайными семенами")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []

    def add(name: str, text_seed: int, chat_seed: int, layout: str, mode: str, purpose: str) -> Path:
        path = out / f"{name}.png"
        render(text_seed, chat_seed, layout, mode).save(path, "PNG")
        manifest.append(
            {
                "name": name,
                "file": path.name,
                "text_seed": text_seed,
                "chat_seed": chat_seed,
                "layout": layout,
                "mode": mode,
                "purpose": purpose,
            }
        )
        return path

    for sample in SAMPLES:
        add(*sample)
    first = out / "01-desktop-unique.png"
    # Тот же файл байт в байт под другим именем: exact_file → duplicate.
    shutil.copyfile(first, out / "04-same-file-as-01.png")
    manifest.append(
        {
            "name": "04-same-file-as-01",
            "file": "04-same-file-as-01.png",
            "text_seed": 11,
            "chat_seed": 11,
            "layout": "desktop",
            "mode": "normal",
            "purpose": "Копия 01 байт в байт: duplicate (exact_file)",
        }
    )
    # Перекодированная копия: другой sha256, те же поля и почти тот же dHash → duplicate (case_key).
    with Image.open(first) as source:
        source.convert("RGB").save(out / "05-recoded-copy-of-01.jpg", "JPEG", quality=85)
    manifest.append(
        {
            "name": "05-recoded-copy-of-01",
            "file": "05-recoded-copy-of-01.jpg",
            "text_seed": 11,
            "chat_seed": 11,
            "layout": "desktop",
            "mode": "normal",
            "purpose": "01 в JPEG q85: другой файл, тот же кейс → duplicate",
        }
    )
    rng = random.Random()
    for index in range(args.random):
        seed = rng.randrange(100, 2048)
        layout = "mobile" if index % 3 == 2 else "desktop"
        add(
            f"r{index + 1:02d}-{layout}-{seed}",
            seed,
            rng.randrange(100, 2048),
            layout,
            "normal",
            "Случайный уникальный",
        )
    manifest.sort(key=lambda item: item["name"])
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(manifest)} скриншотов в {out}")


if __name__ == "__main__":
    main()
