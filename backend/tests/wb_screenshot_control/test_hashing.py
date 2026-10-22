from io import BytesIO

import pytest
from PIL import Image

from backend.modules.wb_screenshot_control.application.thumbnails import THUMB_MAX_WIDTH, render
from backend.modules.wb_screenshot_control.domain.hashing import dhash, from_bits, hamming, to_bits
from backend.tests.wb_screenshot_control.fakes import encode, screen


def hash_of(data: bytes) -> int:
    with Image.open(BytesIO(data)) as image:
        return dhash(image)


@pytest.mark.parametrize("seed", range(4))
def test_the_same_screen_after_resize_and_recompression_stays_close(seed: int) -> None:
    original = screen(seed)
    with Image.open(BytesIO(original)) as image:
        shrunk = encode(image.resize((960, 600)), "JPEG", 60)
        recoded = encode(image.convert("RGB"), "WEBP", 50)
    assert hamming(hash_of(original), hash_of(shrunk)) <= 6
    assert hamming(hash_of(original), hash_of(recoded)) <= 6


def test_different_screens_in_the_same_layout_are_far_apart() -> None:
    hashes = [hash_of(screen(seed)) for seed in range(6)]
    distances = [hamming(left, right) for index, left in enumerate(hashes) for right in hashes[index + 1 :]]
    assert min(distances) >= 13


def test_bits_round_trip_and_reject_garbage() -> None:
    value = hash_of(screen(7))
    assert from_bits(to_bits(value)) == value
    assert len(to_bits(0)) == 64
    with pytest.raises(ValueError):
        to_bits(1 << 64)
    with pytest.raises(ValueError):
        from_bits("01" * 31)


def test_render_gives_the_hash_and_a_jpeg_thumbnail_no_wider_than_1280() -> None:
    wide = screen(3, width=2560, height=1440)
    rendered = render(wide)
    assert rendered.dhash == hash_of(wide)
    with Image.open(BytesIO(rendered.thumbnail)) as thumb:
        assert thumb.format == "JPEG"
        assert thumb.size == (THUMB_MAX_WIDTH, 720)

    narrow = render(screen(3, width=800, height=1600))
    with Image.open(BytesIO(narrow.thumbnail)) as thumb:
        assert thumb.size == (800, 1600)


def test_render_flattens_transparency_onto_white() -> None:
    image = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
    with Image.open(BytesIO(render(encode(image)).thumbnail)) as thumb:
        assert thumb.mode == "RGB"
        assert thumb.getpixel((10, 10)) == (255, 255, 255)
