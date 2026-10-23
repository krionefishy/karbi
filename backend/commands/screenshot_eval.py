"""Офлайн-оценка распознавания и дедупа скриншотов ДБС на размеченной выборке (архитектура, раздел 18).

    uv run python -m backend.commands.screenshot_eval --images DIR --labels labels.jsonl --out report.json \\
        [--model Qwen/Qwen3.6-35B-A3B] [--limit N] [--concurrency 4] [--sweep text_dup=0.8:0.95:0.01]

Прогоняет НАСТОЯЩИЙ `Extractor` (Cloud.ru, ключ `FOUNDATION_MODELS_API_KEY` из окружения, модель из
`vision.model` или `--model`) и настоящий `decide` на кандидатах в памяти — тех же, что вернул бы
репозиторий, только без базы. Каждый файл стоит одного-двух запросов к модели: на сотне скринов это
несколько минут и порядка 300 тыс. токенов.

Разметка — JSONL, одна строка на файл:

    {"file": "a.png", "layout": "desktop_window|mobile_app|other", "chat_id": "<GUID>|null",
     "review_time": "2026-10-22T06:39" (МСК, если без пояса) | null, "review_text": "…", "last_client_text": "…",
     "duplicate_of": "b.png" (необязательно), "user": "anna" (необязательно), "outcome": "positive",
     "channel": "web|extension|telegram", "url": "<адрес вкладки для extension>",
     "received_at": "2026-10-23T12:00+03:00" (от него считаются «вчера» и даты без года; по умолчанию — mtime)}

Ключ кейса — текст отзыва у окна браузера и последняя реплика покупателя у телефона, поэтому
текст сравнивается с `review_text` или `last_client_text` по раскладке из разметки.

Скриншоты и разметка — персональные данные покупателей: держите их вне репозитория (он публичный).
В отчёт тексты не пишутся, только метрики, имена файлов и коды причин.
"""

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from backend.modules.wb_screenshot_control.application.extractor import Extractor
from backend.modules.wb_screenshot_control.application.thumbnails import render
from backend.modules.wb_screenshot_control.domain.decide import decide
from backend.modules.wb_screenshot_control.domain.normalize import normalize_text
from backend.modules.wb_screenshot_control.domain.pipeline_types import (
    Candidate,
    ClientHints,
    DecisionInput,
    ExtractionResult,
)
from backend.modules.wb_screenshot_control.domain.similarity import text_similarity
from backend.shared.llm import LLMClient, LLMError
from backend.shared.settings import Settings, load_settings
from backend.shared.settings.config import ScreenshotControlConfig, ScreenshotThresholds

TEXT_OK = 0.9
FIELDS = ("chat_id", "time", "text")


@dataclass(frozen=True, slots=True)
class Label:
    file: str
    layout: str | None = None
    chat_id: str | None = None
    review_time: str | None = None
    review_text: str | None = None
    last_client_text: str | None = None
    duplicate_of: str | None = None
    user: str = "manager"
    outcome: str = "positive"
    channel: str = "web"
    url: str | None = None
    received_at: str | None = None


@dataclass(slots=True)
class Item:
    label: Label
    data: bytes
    received_at: datetime
    result: ExtractionResult | None = None
    error: str | None = None
    wall_ms: int = 0
    scores: dict[str, Any] = field(default_factory=dict)


def read_labels(path: Path, limit: int | None) -> list[Label]:
    known = {item.name for item in fields(Label)}
    labels = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict) or not raw.get("file"):
            raise ValueError(f"{path}:{number}: нужна строка-объект с полем file")
        labels.append(Label(**{key: value for key, value in raw.items() if key in known and value is not None}))
        if limit is not None and len(labels) >= limit:
            break
    return labels


def _moment(value: str | None, timezone: ZoneInfo) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(value)
    return moment.replace(tzinfo=timezone) if moment.tzinfo is None else moment


def load_items(images: Path, labels: Sequence[Label], timezone: ZoneInfo) -> list[Item]:
    items = []
    for label in labels:
        path = images / label.file
        received = _moment(label.received_at, timezone) or datetime.fromtimestamp(path.stat().st_mtime, UTC)
        items.append(Item(label, path.read_bytes(), received.astimezone(UTC)))
    return items


async def extract_all(extractor: Extractor, items: Sequence[Item], concurrency: int) -> None:
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(item: Item) -> None:
        async with gate:
            started = time.monotonic()
            try:
                item.result = await extractor.extract(
                    item.data,
                    channel=item.label.channel,
                    hints=ClientHints(url=item.label.url),
                    received_at=item.received_at,
                )
            except LLMError as error:
                item.error = f"{type(error).__name__}: {error}"[:300]
            item.wall_ms = round((time.monotonic() - started) * 1000)

    await asyncio.gather(*(one(item) for item in items))


# --- распознавание ---------------------------------------------------------------------------------------


def score_item(item: Item, timezone: ZoneInfo) -> dict[str, Any]:
    """Сравнение с разметкой по одному файлу: только числа и флаги, без текстов."""
    label = item.label
    fields_ = item.result.fields if item.result is not None and item.result.status == "ok" else None
    scores: dict[str, Any] = {
        "file": label.file,
        "status": "error" if item.error else (item.result.status if item.result else "error"),
        "error": item.error or (item.result.error if item.result else None),
        "layout_expected": label.layout,
        "layout": fields_.layout if fields_ else None,
    }
    if fields_ is None:
        return scores
    expected_chat = label.chat_id.lower() if label.chat_id else None
    scores["chat_id_empty"] = fields_.chat_id is None
    scores["chat_id_ok"] = (fields_.chat_id or None) == expected_chat  # у телефона ожидается пусто
    expected_time = _moment(label.review_time, timezone)
    scores["time_empty"] = fields_.review_at is None
    if expected_time is not None:
        got = fields_.review_at
        scores["time_ok"] = got is not None and got.astimezone(UTC).replace(second=0) == expected_time.astimezone(
            UTC
        ).replace(second=0, microsecond=0)
    mobile = (label.layout or fields_.layout) == "mobile_app"
    expected_text = (label.last_client_text if mobile else label.review_text) or label.review_text
    scores["text_empty"] = not fields_.text_norm
    if expected_text:
        s_text = text_similarity(normalize_text(expected_text), fields_.text_norm)
        scores["s_text"] = round(s_text, 4)
        scores["text_ok"] = s_text >= TEXT_OK
    scores["agreement"] = {name: round(value, 4) for name, value in fields_.agreement.items()}
    scores["disagreements"] = list(fields_.disagreements)
    return scores


def _share(values: Sequence[bool]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _percentile(values: Sequence[int], share: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1)))]


def extraction_report(items: Sequence[Item]) -> dict[str, Any]:
    scored = [item.scores for item in items]
    ok = [score for score in scored if score["status"] == "ok"]
    results = [item.result for item in items if item.result is not None]

    def flags(name: str) -> list[bool]:
        return [bool(score[name]) for score in ok if score.get(name) is not None]

    texts = [score["s_text"] for score in ok if "s_text" in score]
    agreement: dict[str, Any] = {}
    for name in FIELDS:
        values = [score["agreement"][name] for score in ok if name in score.get("agreement", {})]
        disagreed = [name in score["disagreements"] for score in ok]
        # Калибровка: точность поля там, где два чтения согласны, и там, где разошлись.
        ok_name = {"chat_id": "chat_id_ok", "time": "time_ok", "text": "text_ok"}[name]
        agreed_ok = [bool(s[ok_name]) for s in ok if s.get(ok_name) is not None and name not in s["disagreements"]]
        split_ok = [bool(s[ok_name]) for s in ok if s.get(ok_name) is not None and name in s["disagreements"]]
        agreement[name] = {
            "measured": len(values),
            "mean": round(statistics.mean(values), 4) if values else None,
            "disagreement_share": _share(disagreed),
            "accuracy_when_agreed": _share(agreed_ok),
            "accuracy_when_disagreed": _share(split_ok),
        }
    latencies = [result.latency_ms for result in results if result.latency_ms is not None]
    tokens_in = sum(result.tokens_in or 0 for result in results)
    tokens_out = sum(result.tokens_out or 0 for result in results)
    return {
        "files": len(items),
        "ok": len(ok),
        "failed": sum(1 for score in scored if score["status"] == "failed"),
        "errors": sum(1 for score in scored if score["status"] == "error"),
        "layout_accuracy": _share([s["layout"] == s["layout_expected"] for s in ok if s["layout_expected"]]),
        "chat_id_accuracy": _share(flags("chat_id_ok")),
        "time_accuracy": _share(flags("time_ok")),
        "text_ok_share": _share(flags("text_ok")),
        "text_similarity_mean": round(statistics.mean(texts), 4) if texts else None,
        "empty_share": {name: _share(flags(f"{name}_empty")) for name in FIELDS},
        "agreement": agreement,
        "tokens": {
            "in": tokens_in,
            "out": tokens_out,
            "per_file": round((tokens_in + tokens_out) / len(results), 1) if results else None,
        },
        "latency_ms": {
            "model_p50": _percentile(latencies, 0.5),
            "model_p95": _percentile(latencies, 0.95),
            "wall_p50": _percentile([item.wall_ms for item in items], 0.5),
            "wall_p95": _percentile([item.wall_ms for item in items], 0.95),
        },
    }


# --- дедуп -----------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Prepared:
    item: Item
    sha256: str
    dhash: int | None


def prepare(items: Sequence[Item]) -> list[Prepared]:
    prepared = []
    for item in sorted(items, key=lambda value: (value.received_at, value.label.file)):
        try:
            dhash: int | None = render(item.data).dhash
        except OSError:
            dhash = None
        prepared.append(Prepared(item, hashlib.sha256(item.data).hexdigest(), dhash))
    return prepared


def dedupe_report(prepared: Sequence[Prepared], cfg: ScreenshotControlConfig) -> dict[str, Any]:
    """Файлы по порядку поступления через настоящий `decide`; кандидаты — все уже решённые (порт Candidate)."""
    users: dict[str, int] = {}
    pool: list[Candidate] = []
    by_id: dict[int, str] = {}
    verdicts: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(prepared, start=1):
        label = entry.item.label
        user = users.setdefault(label.user, len(users) + 1)
        submission_id = _uuid(index)
        result = entry.item.result or ExtractionResult(status="failed", error=entry.item.error or "no result")
        decision = decide(
            DecisionInput(
                submission_id=submission_id,
                user_id=_uuid(10_000 + user),
                channel=label.channel,  # type: ignore[arg-type]
                received_at=entry.item.received_at,
                outcome=label.outcome,
                file_sha256=entry.sha256,
                dhash=entry.dhash,
                extraction=result,
                hints=ClientHints(url=label.url),
            ),
            pool,
            cfg,
        )
        by_id[index] = label.file
        root = by_id.get(decision.duplicate_of.int) if decision.duplicate_of else None
        verdicts[label.file] = {
            "verdict": decision.verdict,
            "duplicate_of": root,
            "reasons": [reason.code for reason in decision.reasons],
        }
        if decision.verdict != "rejected":
            pool.append(
                Candidate(
                    submission_id=submission_id,
                    user_id=_uuid(10_000 + user),
                    received_at=entry.item.received_at,
                    verdict=decision.verdict,
                    outcome=label.outcome,
                    file_sha256=entry.sha256,
                    dhash=entry.dhash,
                    fields=result.fields if result.status == "ok" else None,
                )
            )
    return _confusion(prepared, verdicts)


def _uuid(number: int) -> uuid.UUID:
    return uuid.UUID(int=number)


def _confusion(prepared: Sequence[Prepared], verdicts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    false_positives, false_negatives = [], []
    review = {"duplicates": 0, "unique": 0}
    for entry in prepared:
        label = entry.item.label
        got = verdicts[label.file]
        actual = bool(label.duplicate_of)
        predicted = got["verdict"] == "duplicate"
        if got["verdict"] == "needs_review":
            review["duplicates" if actual else "unique"] += 1
        if predicted and actual:
            tp += 1
        elif predicted:
            fp += 1
            false_positives.append({"file": label.file, "matched": got["duplicate_of"], "reasons": got["reasons"]})
        elif actual:
            fn += 1
            false_negatives.append(
                {
                    "file": label.file,
                    "duplicate_of": label.duplicate_of,
                    "verdict": got["verdict"],
                    "reasons": got["reasons"],
                }
            )
        else:
            tn += 1
    return {
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "counts": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "needs_review": review,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "verdicts": verdicts,
    }


def parse_sweep(spec: str) -> tuple[str, list[float]]:
    """`text_dup=0.8:0.95:0.01` → имя порога и значения включительно."""
    name, _, span = spec.partition("=")
    known = {item.name for item in fields(ScreenshotThresholds)}
    if name not in known:
        raise ValueError(f"Неизвестный порог {name!r}; есть: {', '.join(sorted(known))}")
    start, stop, step = (float(part) for part in span.split(":"))
    if step <= 0 or stop < start:
        raise ValueError("Нужно start ≤ stop и step > 0")
    values = []
    current = start
    while current <= stop + step / 1000:
        values.append(round(current, 6))
        current += step
    return name, values


def sweep_report(prepared: Sequence[Prepared], cfg: ScreenshotControlConfig, spec: str) -> dict[str, Any]:
    name, values = parse_sweep(spec)
    rows = []
    for value in values:
        typed: float | int = int(value) if isinstance(getattr(cfg.thresholds, name), int) else value
        overrides: dict[str, Any] = {name: typed}
        run = dedupe_report(prepared, replace(cfg, thresholds=replace(cfg.thresholds, **overrides)))
        rows.append(
            {
                name: typed,
                "precision": run["precision"],
                "recall": run["recall"],
                **run["counts"],
                "needs_review": sum(run["needs_review"].values()),
            }
        )
    return {"threshold": name, "rows": rows}


# --- запуск ----------------------------------------------------------------------------------------------


async def evaluate(
    settings: Settings,
    images: Path,
    labels_path: Path,
    *,
    model: str | None = None,
    limit: int | None = None,
    concurrency: int = 4,
    sweeps: Sequence[str] = (),
) -> dict[str, Any]:
    screens = replace(settings.screenshot_control, audit_percent=0.0)
    vision = replace(settings.vision, model=model) if model else settings.vision
    timezone = ZoneInfo(screens.timezone)
    items = load_items(images, read_labels(labels_path, limit), timezone)
    extractor = Extractor(
        LLMClient(vision),
        timezone=screens.timezone,
        hmac_key=screens.hmac_key or "screenshot-eval",
        text_threshold=screens.thresholds.vlm_ocr_text,
        max_image_side=vision.max_image_side,
        repair_model=vision.fallback_model or None,
    )
    started = time.monotonic()
    await extract_all(extractor, items, concurrency)
    for item in items:
        item.scores = score_item(item, timezone)
    prepared = prepare(items)
    return {
        "model": vision.model,
        "prompt": "v1",
        "pipeline_version": screens.pipeline_version,
        "thresholds": {item.name: getattr(screens.thresholds, item.name) for item in fields(ScreenshotThresholds)},
        "wall_s": round(time.monotonic() - started, 1),
        "extraction": extraction_report(items),
        "dedupe": dedupe_report(prepared, screens),
        "sweeps": [sweep_report(prepared, screens, spec) for spec in sweeps],
        "files": [item.scores for item in items],
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Оценка распознавания и дедупа скриншотов ДБС на разметке")
    parser.add_argument("--images", type=Path, required=True, help="папка со скриншотами (вне репозитория)")
    parser.add_argument("--labels", type=Path, required=True, help="разметка JSONL")
    parser.add_argument("--out", type=Path, required=True, help="куда записать отчёт JSON")
    parser.add_argument("--model", help="модель вместо vision.model")
    parser.add_argument("--limit", type=int, help="только первые N строк разметки")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--sweep", action="append", default=[], help="порог=start:stop:step, можно несколько")
    parser.add_argument("--config", help="путь к конфигу вместо CONFIG_PATH")
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    key = settings.vision.api_key or os.environ.get("FOUNDATION_MODELS_API_KEY", "")
    if not key:
        parser.error("нет ключа модели: задайте FOUNDATION_MODELS_API_KEY")
    settings = replace(settings, vision=replace(settings.vision, api_key=key))
    for spec in args.sweep:
        try:
            parse_sweep(spec)
        except ValueError as error:
            parser.error(f"--sweep {spec}: {error}")
    report = asyncio.run(
        evaluate(
            settings,
            args.images,
            args.labels,
            model=args.model,
            limit=args.limit,
            concurrency=args.concurrency,
            sweeps=args.sweep,
        )
    )
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    extraction, dedupe = report["extraction"], report["dedupe"]
    sys.stdout.write(
        f"{extraction['ok']}/{extraction['files']} распознано; chat_id {extraction['chat_id_accuracy']}, "
        f"время {extraction['time_accuracy']}, текст≥{TEXT_OK} {extraction['text_ok_share']}; "
        f"дубли: precision {dedupe['precision']}, recall {dedupe['recall']} → {args.out}\n"
    )


if __name__ == "__main__":
    main()
