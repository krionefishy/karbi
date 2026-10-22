"""Подготовка картинок для модели: кадр целиком и вырезка адресной строки.

Модель на стороне Cloud.ru всё равно ужимает картинку примерно до 1000 токенов, поэтому кадр уменьшаем до
`vision.max_image_side` (без апскейла), а адресную строку, где мелкий chatId, вырезаем и увеличиваем:
отдельное чтение вырезки ошибается на других скринах, чем чтение кадра (этап 0), и служит вторым мнением.
"""

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from backend.shared.llm import ImagePart

# Адресная строка Chrome — под полосой вкладок: у окна 1920×1080 примерно 40–84 px сверху (7,8 %), у полного
# экрана ретины-Mac со строкой меню и «чёлкой» — до ~245 px из 1800 (13,6 %). Берём верхние 15 %, но не меньше
# 200 px, на всю ширину: в узком окне адрес с chatId доходит почти до правого края.
ADDRESS_BAR_BOTTOM = 0.15
ADDRESS_BAR_MIN_PX = 200
ADDRESS_BAR_SCALE = 2.0
ADDRESS_BAR_MAX_SIDE = 2800
# Тело запроса больше ~8 МБ Cloud.ru не принимает (этап 0): крупный PNG пересжимаем в JPEG.
MAX_PNG_BYTES = 4_000_000


class ImageUnreadable(ValueError):
    """Байты не открываются как картинка: повтор не поможет."""


@dataclass(frozen=True, slots=True)
class Frame:
    image: Image.Image

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size


def open_frame(data: bytes) -> Frame:
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise ImageUnreadable("картинка не открывается") from error
    return Frame(image)


def full_frame_part(frame: Frame, max_side: int) -> ImagePart:
    """Кадр целиком, длинная сторона не больше `max_side`; маленький не увеличиваем."""
    image = frame.image
    longest = max(image.size)
    if longest > max_side:
        scale = max_side / longest
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS
        )
    return _encode(image)


def address_bar_box(width: int, height: int) -> tuple[int, int, int, int]:
    return 0, 0, width, min(height, max(round(height * ADDRESS_BAR_BOTTOM), ADDRESS_BAR_MIN_PX))


def address_bar_part(frame: Frame) -> ImagePart:
    """Верх окна с адресной строкой, увеличенный до двух раз (длинная сторона не больше `ADDRESS_BAR_MAX_SIDE`)."""
    crop = frame.image.crop(address_bar_box(*frame.size))
    scale = min(ADDRESS_BAR_SCALE, ADDRESS_BAR_MAX_SIDE / max(crop.size))
    if scale != 1:
        crop = crop.resize(
            (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))), Image.Resampling.LANCZOS
        )
    return _encode(crop)


def _encode(image: Image.Image) -> ImagePart:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    if buffer.tell() <= MAX_PNG_BYTES:
        return ImagePart(buffer.getvalue(), "image/png")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=92)
    return ImagePart(buffer.getvalue(), "image/jpeg")
