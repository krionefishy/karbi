"""Поддельная модель локального стенда (deploy/stand/fake_vlm.py) отвечает строго по схеме промпта.

Если схема `ScreenshotAnswerV1` изменится, а фейк нет, стенд начнёт отправлять всё в «распознать не
получилось»: этот тест упадёт раньше.
"""

import base64
import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType

import pytest
from PIL import Image

from backend.modules.wb_screenshot_control.domain.prompt import AddressBarAnswerV1, ScreenshotAnswerV1

FAKE = Path(__file__).resolve().parents[3] / "deploy" / "stand" / "fake_vlm.py"


@pytest.fixture(scope="module")
def fake() -> ModuleType:
    spec = importlib.util.spec_from_file_location("stand_fake_vlm", FAKE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("layout", ["desktop_window", "mobile_app"])
def test_answer_matches_schema(fake: ModuleType, layout: str) -> None:
    answer = fake.screenshot_answer(fake.facts(11, 11), layout)
    parsed = ScreenshotAnswerV1.model_validate(answer)
    assert parsed.layout == layout
    if layout == "desktop_window":
        assert parsed.chat_id.value == fake.chat_guid(11)
        assert parsed.review.text == fake.facts(11, 11)["text"]
    else:
        assert parsed.last_client_message.present


def test_same_seeds_same_fields_other_seeds_differ(fake: ModuleType) -> None:
    assert fake.facts(12, 12) == fake.facts(12, 12)
    assert fake.facts(12, 12)["text"] != fake.facts(13, 13)["text"]
    assert fake.chat_guid(12) != fake.chat_guid(13)


def test_mark_survives_jpeg(fake: ModuleType) -> None:
    image = Image.new("RGB", (1200, 800), (200, 180, 220))
    bits = [*fake.MARK_SYNC, *fake.encode_mark(321, 1234, "fail")]
    y = fake.mark_y(image.height)
    for index, bit in enumerate(bits):
        x = fake.MARK_X + index * fake.MARK_BLOCK
        image.paste((255, 255, 255) if bit else (0, 0, 0), (x, y, x + fake.MARK_BLOCK, y + fake.MARK_BLOCK))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=80)
    assert fake.decode_mark(Image.open(io.BytesIO(buffer.getvalue()))) == (321, 1234, "fail")
    assert fake.decode_mark(Image.new("RGB", (1200, 800), "white")) is None


def test_address_bar_answer_matches_schema(fake: ModuleType) -> None:
    facts = fake.facts(5, 7)
    AddressBarAnswerV1.model_validate({"url": facts["url"], "chat_id": facts["chat_id"]})


def test_times_follow_the_rule_not_its_position_in_the_file(fake: ModuleType, tmp_path: Path) -> None:
    """Правка scenarios.json на ходу: новое правило на месте сработавшего не должно считаться сработавшим."""
    scenarios = tmp_path / "scenarios.json"
    fake.__dict__["STATE"] = fake.State(str(scenarios), str(tmp_path / "manifest.json"))
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 200, 200)).save(buffer, format="PNG")
    image = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    body = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": image}}]}]}

    scenarios.write_text(json.dumps({"rules": [{"match": {"kind": "frame"}, "error": 503, "times": 1}]}))
    assert fake.respond(body)[0] == 503
    assert fake.respond(body)[0] == 200
    scenarios.write_text(json.dumps({"rules": [{"match": {"kind": "frame"}, "error": 429, "times": 1}]}))
    assert fake.respond(body)[0] == 429
