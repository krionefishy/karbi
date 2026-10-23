"""Команда офлайн-оценки на пяти синтетических скринах: модель — respx, в Cloud.ru ничего не уходит."""

import asyncio
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from backend.commands import screenshot_eval
from backend.tests.wb_screenshot_control.fakes import screen
from backend.tests.wb_screenshot_control.test_end_to_end import (
    CHAT,
    OTHER_CHAT,
    OTHER_REVIEW,
    REVIEW,
    THIRD_CHAT,
    Vlm,
    chat_url,
    desktop,
    mobile,
)

CLOUD_URL = "https://foundation-models.api.cloud.ru/v1/chat/completions"
CONFIG = "backend/shared/settings/config.test.yaml"
MSK = ZoneInfo("Europe/Moscow")
RECEIVED = "2026-10-23T12:00:00+03:00"
PHONE = [
    ("client", "Здравствуйте, пришла куртка с порванным швом на рукаве", "21:05"),
    ("seller", "Добрый вечер! Пришлите фото", "21:10"),
    ("client", "Фото приложила, шов разошёлся сантиметров на пять", "21:15"),
]


def dataset(root: Path) -> tuple[Path, Path, Vlm]:
    """a — честный, b — пересжатая копия a другим менеджером, c — chatId прочитан не тот, d — телефон,
    e — модель отвечает 500."""
    images = root / "images"
    images.mkdir()
    vlm = Vlm()
    moment = datetime(2026, 10, 22, 6, 39, tzinfo=MSK)
    a = screen(1)
    files = {
        "a.png": vlm.register(a, desktop(CHAT, REVIEW, moment), {"url": chat_url(CHAT), "chat_id": CHAT}),
        "b.jpg": vlm.register(
            screen(1, fmt="JPEG", quality=80), desktop(CHAT, REVIEW, moment), {"url": chat_url(CHAT), "chat_id": CHAT}
        ),
        "c.png": vlm.register(
            screen(2),
            desktop(OTHER_CHAT, OTHER_REVIEW, datetime(2026, 10, 22, 9, 15, tzinfo=MSK)),
            {"url": chat_url(OTHER_CHAT), "chat_id": OTHER_CHAT},
        ),
        "d.png": vlm.register(screen(3, width=600, height=1300), mobile(PHONE)),
        "e.png": vlm.register(screen(4), httpx.Response(500, json={"error": "down"})),
    }
    for name, data in files.items():
        (images / name).write_bytes(data)
    labels: list[dict[str, Any]] = [
        {"file": "a.png", "layout": "desktop_window", "chat_id": CHAT, "review_time": "2026-10-22T06:39",
         "review_text": REVIEW},
        {"file": "b.jpg", "layout": "desktop_window", "chat_id": CHAT, "review_time": "2026-10-22T06:39",
         "review_text": REVIEW, "duplicate_of": "a.png", "user": "other"},
        {"file": "c.png", "layout": "desktop_window", "chat_id": THIRD_CHAT, "review_time": "2026-10-22T09:15",
         "review_text": OTHER_REVIEW},
        {"file": "d.png", "layout": "mobile_app", "chat_id": None, "review_time": "2026-10-22T21:15",
         "last_client_text": PHONE[-1][1]},
        {"file": "e.png", "layout": "desktop_window", "chat_id": CHAT, "review_time": None, "review_text": "x"},
    ]  # fmt: skip
    path = root / "labels.jsonl"
    path.write_text(
        "\n".join(json.dumps({**label, "received_at": RECEIVED}, ensure_ascii=False) for label in labels),
        encoding="utf-8",
    )
    return images, path, vlm


def test_the_report_scores_fields_and_duplicates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    images, labels, vlm = dataset(tmp_path)
    out = tmp_path / "report.json"
    monkeypatch.setenv("FOUNDATION_MODELS_API_KEY", "secret")

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(CLOUD_URL).mock(side_effect=vlm)
        screenshot_eval.main(
            [
                "--config", CONFIG, "--images", str(images), "--labels", str(labels), "--out", str(out),
                "--concurrency", "2", "--sweep", "text_dup=0.8:0.95:0.05",
            ]
        )  # fmt: skip
    assert route.calls[0].request.headers["Authorization"] == "Bearer secret"

    report = json.loads(out.read_text(encoding="utf-8"))
    extraction = report["extraction"]
    assert (extraction["files"], extraction["ok"], extraction["errors"]) == (5, 4, 1)
    assert extraction["layout_accuracy"] == 1.0
    assert extraction["chat_id_accuracy"] == 0.75  # c прочитан как OTHER_CHAT, а в разметке THIRD_CHAT
    assert extraction["time_accuracy"] == 1.0 and extraction["text_ok_share"] == 1.0
    assert extraction["text_similarity_mean"] == 1.0
    assert extraction["empty_share"] == {"chat_id": 0.25, "time": 0.0, "text": 0.0}  # у телефона chatId нет
    assert extraction["agreement"]["chat_id"]["mean"] == 1.0
    assert extraction["agreement"]["chat_id"]["accuracy_when_agreed"] == 0.75  # согласны, но оба чтения неверны (c)
    assert extraction["tokens"]["in"] == 1100 * 7  # 3 окна × 2 запроса + телефон
    assert extraction["latency_ms"]["model_p50"] is not None

    dedupe = report["dedupe"]
    assert (dedupe["precision"], dedupe["recall"]) == (1.0, 1.0)
    assert dedupe["counts"] == {"tp": 1, "fp": 0, "fn": 0, "tn": 4}
    assert dedupe["verdicts"]["b.jpg"]["duplicate_of"] == "a.png"
    assert dedupe["verdicts"]["b.jpg"]["reasons"][:2] == ["case_key", "cross_user_duplicate"]
    assert dedupe["verdicts"]["e.png"] == {
        "verdict": "needs_review",
        "duplicate_of": None,
        "reasons": ["extraction_failed"],
    }
    assert dedupe["needs_review"] == {"duplicates": 0, "unique": 1}

    (sweep,) = report["sweeps"]
    assert sweep["threshold"] == "text_dup" and [row["text_dup"] for row in sweep["rows"]] == [0.8, 0.85, 0.9, 0.95]
    assert all(row["recall"] == 1.0 for row in sweep["rows"])

    serialized = out.read_text(encoding="utf-8")
    assert "маломерит" not in serialized and "Анна" not in serialized, "тексты и имена в отчёт не попадают"
    by_file = {item["file"]: item for item in report["files"]}
    assert by_file["c.png"]["chat_id_ok"] is False and by_file["e.png"]["status"] == "error"
    assert "500" in by_file["e.png"]["error"]


def test_a_false_duplicate_is_listed(tmp_path: Path) -> None:
    """Разметка говорит «не дубль», правила говорят «дубль» — в списке ложных срабатываний."""
    images, labels, vlm = dataset(tmp_path)
    rows = [json.loads(line) for line in labels.read_text(encoding="utf-8").splitlines()]
    rows[1].pop("duplicate_of")
    labels.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    settings = screenshot_eval.load_settings(CONFIG)
    settings = replace(settings, vision=replace(settings.vision, api_key="secret"))

    with respx.mock as mock:
        mock.post(CLOUD_URL).mock(side_effect=vlm)
        report = asyncio.run(screenshot_eval.evaluate(settings, images, labels, limit=3))

    dedupe = report["dedupe"]
    assert dedupe["counts"]["fp"] == 1 and dedupe["precision"] == 0.0 and dedupe["recall"] is None
    assert dedupe["false_positives"] == [
        {"file": "b.jpg", "matched": "a.png", "reasons": dedupe["verdicts"]["b.jpg"]["reasons"]}
    ]
    assert report["extraction"]["files"] == 3


@pytest.mark.parametrize("spec", ["nope=0.1:0.2:0.1", "text_dup=0.9:0.8:0.01", "text_dup=0.8:0.9:0"])
def test_a_bad_sweep_is_refused(spec: str) -> None:
    with pytest.raises(ValueError):
        screenshot_eval.parse_sweep(spec)


def test_without_a_key_the_command_refuses_to_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOUNDATION_MODELS_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        screenshot_eval.main(
            [
                "--config",
                CONFIG,
                "--images",
                str(tmp_path),
                "--labels",
                str(tmp_path / "l"),
                "--out",
                str(tmp_path / "o"),
            ]
        )
