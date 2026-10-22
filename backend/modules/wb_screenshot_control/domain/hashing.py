"""dHash всего кадра: 64 бита, устойчив к перекодированию и масштабу, но не к кропу.

Сам по себе дубль не доказывает (у разных отзывов в одном макете интерфейс одинаков) — только
подсказка для решения и повод поискать кандидата.
"""

from PIL import Image

HASH_BITS = 64
_WIDTH, _HEIGHT = 9, 8


def dhash(image: Image.Image) -> int:
    """Серый кадр 9×8, сравнение соседей по горизонтали: бит 1, если левый пиксель ярче."""
    small = image.convert("L").resize((_WIDTH, _HEIGHT), Image.Resampling.LANCZOS)
    pixels = small.tobytes()
    value = 0
    for row in range(_HEIGHT):
        offset = row * _WIDTH
        for column in range(_WIDTH - 1):
            value = (value << 1) | (pixels[offset + column] > pixels[offset + column + 1])
    return value


def hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def to_bits(value: int) -> str:
    """Для колонки `bit(64)`: строка из 64 нулей и единиц."""
    if not 0 <= value < 1 << HASH_BITS:
        raise ValueError("dhash must fit into 64 bits")
    return format(value, f"0{HASH_BITS}b")


def from_bits(bits: str) -> int:
    if len(bits) != HASH_BITS or set(bits) - {"0", "1"}:
        raise ValueError("dhash bits must be 64 characters of 0 and 1")
    return int(bits, 2)
