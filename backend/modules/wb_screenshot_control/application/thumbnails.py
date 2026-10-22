from dataclasses import dataclass
from io import BytesIO

from PIL import Image

from backend.modules.wb_screenshot_control.domain.hashing import dhash

THUMB_MAX_WIDTH = 1280
THUMB_QUALITY = 70


@dataclass(frozen=True, slots=True)
class Rendered:
    dhash: int
    thumbnail: bytes


def render(data: bytes) -> Rendered:
    """Одно декодирование на хеш и миниатюру. Синхронно: вызывать через `asyncio.to_thread`."""
    with Image.open(BytesIO(data)) as image:
        image.load()
        value = dhash(image)
        frame = image.convert("RGBA") if "A" in image.mode or "transparency" in image.info else image
        if frame.mode == "RGBA":
            flat = Image.new("RGB", frame.size, "white")
            flat.paste(frame, mask=frame.getchannel("A"))
            frame = flat
        elif frame.mode != "RGB":
            frame = frame.convert("RGB")
        if frame.width > THUMB_MAX_WIDTH:
            height = max(1, round(frame.height * THUMB_MAX_WIDTH / frame.width))
            frame = frame.resize((THUMB_MAX_WIDTH, height), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        frame.save(buffer, format="JPEG", quality=THUMB_QUALITY, optimize=True)
    return Rendered(dhash=value, thumbnail=buffer.getvalue())
