"""Поддельная модель распознавания для локального стенда: OpenAI-совместимый `/v1/chat/completions`.

Отвечает по схеме `ScreenshotAnswerV1` / `AddressBarAnswerV1` (backend/modules/wb_screenshot_control/domain/prompt.py)
строгим JSON, без сети и без ключа. Поля выводятся детерминированно:

1. Синтетические скрины стенда (deploy/stand/make_samples.py) несут в левом нижнем углу полосу из 34 квадратов
   8×8: метку с двумя «семенами» (текст/время и chatId) и режимом. Метка переживает перекодирование в JPEG,
   поэтому перекодированная копия читается так же, как оригинал, — как её прочитала бы настоящая модель.
2. Любая другая картинка: семена берутся из sha256 присланных байтов (одна картинка — одни поля).

Вырезка адресной строки — верх того же кадра, увеличенный вдвое: фейк находит среди недавних кадров тот, чей
верх больше всего похож на вырезку (воркер распознаёт несколько скринов параллельно), и отвечает его chatId.

Управление — deploy/stand/scenarios.json (перечитывается на каждый запрос), см. docs/STAND.md:
    {"rules": [{"match": {"sample": "08-recognition-error"}, "error": 500},
               {"match": {"request": 12}, "error": "invalid_json"},
               {"match": {"sha256": "<sha256 картинки из лога>"}, "patch": {"chat_id": {"value": null}}},
               {"match": {"kind": "address_bar", "sample": "05-other-chat"}, "answer": {"url": null, "chat_id": null}}]}
`error`: число (HTTP-статус), "timeout", "invalid_json", "empty". `times`: сколько раз правило срабатывает.

Запуск: python deploy/stand/fake_vlm.py [--port 8000]. Pillow нужен только для чтения метки.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")

# --- метка на синтетических скринах --------------------------------------------------------------------

MARK_BLOCK = 8
MARK_X = 8
MARK_BITS = 32
MARK_SYNC = (0, 1)  # чёрный, белый
MODES = {0: "normal", 1: "fail", 2: "invalid_json", 3: "edited"}
MODE_CODES = {name: code for code, name in MODES.items()}


def mark_y(height: int) -> int:
    return height - 2 * MARK_BLOCK


def encode_mark(text_seed: int, chat_seed: int, mode: str = "normal") -> list[int]:
    """Биты метки: 11 бит текста, 11 бит чата, 2 бита режима, 8 бит контрольной суммы."""
    if not (0 <= text_seed < 2048 and 0 <= chat_seed < 2048):
        raise ValueError("семена 0..2047")
    payload = (text_seed << 13) | (chat_seed << 2) | MODE_CODES[mode]
    checksum = (sum(payload.to_bytes(3, "big")) ^ 0xA5) & 0xFF
    word = (payload << 8) | checksum
    return [(word >> (MARK_BITS - 1 - i)) & 1 for i in range(MARK_BITS)]


def decode_mark(image: Any) -> tuple[int, int, str] | None:
    """Метка с картинки Pillow или None. Порог яркости 128, среднее по центру квадрата 4×4."""
    gray = image.convert("L")
    width, height = gray.size
    total = len(MARK_SYNC) + MARK_BITS
    if width < MARK_X + total * MARK_BLOCK or height < 4 * MARK_BLOCK:
        return None
    y0 = mark_y(height)
    bits: list[int] = []
    for index in range(total):
        x0 = MARK_X + index * MARK_BLOCK
        box = gray.crop((x0 + 2, y0 + 2, x0 + 6, y0 + 6))
        pixels = list(box.getdata())
        bits.append(1 if sum(pixels) / len(pixels) >= 128 else 0)
    if tuple(bits[: len(MARK_SYNC)]) != MARK_SYNC:
        return None
    word = 0
    for bit in bits[len(MARK_SYNC) :]:
        word = (word << 1) | bit
    payload, checksum = word >> 8, word & 0xFF
    if (sum(payload.to_bytes(3, "big")) ^ 0xA5) & 0xFF != checksum:
        return None
    return payload >> 13, (payload >> 2) & 0x7FF, MODES[payload & 0b11]


# --- детерминированные поля ----------------------------------------------------------------------------

REVIEWS = (
    "Товар пришёл с опозданием на неделю, упаковка помята, курьер не позвонил заранее",
    "Размер не подошёл, хотя брала по таблице, ткань тоньше чем на фото, возвращать долго",
    "Цвет совсем другой, на фото тёмно-синий, а пришёл почти чёрный, очень расстроена",
    "Не работает одна из кнопок, пришлось нести в сервис, продавец на сообщения не отвечал",
    "Запах резины сильный, проветривала три дня, всё равно пахнет, ребёнку не дала",
    "Коробка была вскрыта, внутри не хватало инструкции и одного крепления из комплекта",
    "Ожидала лучшего качества за такую цену, швы кривые, нитки торчат по всему краю",
    "Доставили не тот товар, артикул совпадает, а модель другая, теперь жду обмен",
    "Батарея держит меньше часа, в описании обещали пять, верните деньги пожалуйста",
    "Крышка треснула при первом же открытии, пластик хрупкий, на холоде вообще ломается",
    "Курьер оставил заказ у двери без звонка, хорошо что соседи забрали, товар мокрый",
    "Сидит плохо, рукава короткие, в плечах узко, в отзывах писали что маломерит, так и есть",
)
DETAILS = (
    "брали в подарок маме на день рождения, теперь неудобно перед ней",
    "второй раз заказываю у этого продавца и второй раз разочарование",
    "на пункте выдачи сразу открыли, продавщица тоже удивилась",
    "в карточке написано одно, по факту совсем другое, проверьте описание",
    "ждали почти три недели, хотя обещали доставку за четыре дня",
    "фото прикладываю, видно что брак заводской, а не от доставки",
    "пользовались аккуратно, ребёнок даже не успел толком поиграть",
    "звонила в поддержку, сказали писать продавцу, вот пишу сюда",
    "у подруги такой же заказ, у неё всё нормально, значит лотерея",
    "оценку поставила низкую, исправлю, если решите вопрос по-человечески",
    "упаковка без пупырки, просто пакет, неудивительно что всё побилось",
    "по размерной сетке брала L, а пришло как будто S",
)
FOLLOW_UPS = (
    "Добрый день, отзыв исправлю, если вернёте деньги за брак",
    "Спасибо за ответ, обмен устроит, пришлите инструкцию",
    "Я уже отправила фото, когда будет решение по возврату?",
    "Хорошо, жду курьера, отзыв поменяю после обмена",
    "Деньги получила, спасибо, исправила оценку",
    "Нет, такой вариант не подходит, хочу полный возврат",
)
SELLER_REPLIES = (
    "Здравствуйте! Нам очень жаль. Готовы вернуть деньги или заменить товар — что удобнее?",
    "Добрый день! Пришлите, пожалуйста, фото дефекта, оформим возврат сегодня же.",
    "Здравствуйте, приносим извинения. Отправим замену за наш счёт.",
)
NAMES = ("Анна", "Мария", "Ольга", "Екатерина", "Ирина", "Наталья", "Светлана", "Дмитрий", "Сергей", "Алексей")
PRODUCTS = (
    "Куртка демисезонная женская",
    "Наушники беспроводные",
    "Термокружка 450 мл",
    "Набор контейнеров для еды",
    "Детский рюкзак",
    "Настольная лампа LED",
)
MONTHS_GEN = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def _h(*parts: object) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "big")


def chat_guid(chat_seed: int) -> str:
    raw = hashlib.sha256(f"stand-chat-{chat_seed}".encode()).hexdigest()
    return f"{raw[:8]}-{raw[8:12]}-4{raw[13:16]}-a{raw[17:20]}-{raw[20:32]}"


def chat_url(chat_seed: int) -> str:
    return f"https://seller.wildberries.ru/chat-with-clients?chatId={chat_guid(chat_seed)}"


def facts(text_seed: int, chat_seed: int, today: date | None = None) -> dict[str, Any]:
    """Всё, что «видно» на скрине: и фейк, и генератор картинок берут поля отсюда."""
    today = today or datetime.now(MSK).date()
    # Вчера и раньше: отзыв «сегодня в 19:40», присланный в 15:00, — дата из будущего, это needs_review.
    review_day = today - timedelta(days=1 + _h("day", text_seed) % 3)
    hour, minute = 8 + _h("hour", text_seed) % 12, _h("minute", text_seed) % 60
    # Две независимые части: у разных семян тексты расходятся, и сравнение текста не видит в них дубль.
    text = f"{REVIEWS[_h('review', text_seed) % len(REVIEWS)]}, {DETAILS[_h('detail', text_seed) % len(DETAILS)]}"
    order = 100000 + _h("order", text_seed) % 900000
    reply_minute = (minute + 7) % 60
    reply_hour = hour + (minute + 7) // 60
    follow_minute = (minute + 23) % 60
    follow_hour = hour + (minute + 23) // 60
    return {
        "text_seed": text_seed,
        "chat_seed": chat_seed,
        "chat_id": chat_guid(chat_seed),
        "url": chat_url(chat_seed),
        "date": review_day,
        "date_text": f"{review_day.day} {MONTHS_GEN[review_day.month - 1]} {review_day.year}",
        "hour": hour,
        "minute": minute,
        "time_text": f"{hour:02d}:{minute:02d}",
        "stars": 1 + _h("stars", text_seed) % 3,
        "text": text,
        "buyer": NAMES[_h("name", text_seed) % len(NAMES)],
        "article": str(100_000_000 + _h("article", text_seed) % 900_000_000),
        "product": PRODUCTS[_h("product", text_seed) % len(PRODUCTS)],
        "seller_reply": SELLER_REPLIES[_h("reply", text_seed) % len(SELLER_REPLIES)],
        "reply_time": f"{reply_hour % 24:02d}:{reply_minute:02d}",
        # Номер заказа в реплике: одинаковые реплики у разных кейсов сравнение переписки сочло бы дублем.
        "follow_up": f"{FOLLOW_UPS[_h('follow', text_seed) % len(FOLLOW_UPS)]}, заказ {order}",
        "follow_time": f"{follow_hour % 24:02d}:{follow_minute:02d}",
    }


def screenshot_answer(f: dict[str, Any], layout: str, *, edited: bool = False) -> dict[str, Any]:
    d: date = f["date"]
    date_text = f["date_text"]
    if layout == "mobile_app":
        client = {
            "present": True,
            "text": f["follow_up"],
            "time_text": f["follow_time"],
            "hour": int(f["follow_time"][:2]),
            "minute": int(f["follow_time"][3:]),
            "date_text": date_text,
            "day": d.day,
            "month": d.month,
            "year": d.year,
            "confidence": 0.93,
        }
        messages = [
            {"side": "client", "text": f["text"], "time_text": f["time_text"], "date_text": date_text},
            {"side": "seller", "text": f["seller_reply"], "time_text": f["reply_time"], "date_text": date_text},
            {"side": "client", "text": f["follow_up"], "time_text": f["follow_time"], "date_text": date_text},
        ]
        review: dict[str, Any] = {"present": False}
        url = chat = {"value": None, "confidence": 0.0}
    else:
        client = {"present": True, "text": f["follow_up"], "time_text": f["follow_time"], "confidence": 0.9}
        messages = [
            {"side": "seller", "text": f["seller_reply"], "time_text": f["reply_time"], "date_text": None},
            {"side": "client", "text": f["follow_up"], "time_text": f["follow_time"], "date_text": None},
        ]
        review = {
            "present": True,
            "date_text": f"{date_text}, {f['time_text']}",
            "day": d.day,
            "month": d.month,
            "year": d.year,
            "hour": f["hour"],
            "minute": f["minute"],
            "date_confidence": 0.95,
            "stars": f["stars"],
            "text": f["text"],
            "text_truncated": False,
            "text_confidence": 0.95,
            "article": f["article"],
            "product_name": f["product"],
        }
        url = {"value": f["url"], "confidence": 0.95}
        chat = {"value": f["chat_id"], "confidence": 0.95}
    return {
        "layout": layout,
        "is_wb_seller_chat": True,
        "url": url,
        "chat_id": chat,
        "review": review,
        "last_client_message": client,
        "messages": messages,
        "buyer_name": {"value": f["buyer"], "confidence": 0.9},
        "article": {"value": f["article"], "confidence": 0.9},
        "quality": {
            "blurred": False,
            "cropped_missing_panel": False,
            "photo_of_screen": False,
            "moire_or_glare": False,
            "looks_edited": edited,
            "dark_overlay_or_popup": False,
        },
        "notes": None,
    }


# --- вырезка адресной строки ----------------------------------------------------------------------------

SIGNATURE_SIZE = (48, 6)


def top_signature(image: Any, crop: bool) -> list[int]:
    """Серый отпечаток верхней полосы. Для кадра режем так же, как воркер (vision.address_bar_box)."""
    gray = image.convert("L")
    if not crop:
        width, height = gray.size
        gray = gray.crop((0, 0, width, min(height, max(round(height * 0.15), 200))))
    return list(gray.resize(SIGNATURE_SIZE).getdata())


def signature_distance(a: list[int], b: list[int]) -> int:
    return sum(abs(x - y) for x, y in zip(a, b, strict=True))


# --- сервер ---------------------------------------------------------------------------------------------


class State:
    def __init__(self, scenarios_path: str, manifest_path: str) -> None:
        self.lock = threading.Lock()
        self.requests = 0
        self.frames: list[dict[str, Any]] = []  # недавние кадры: отпечаток верха, url, chat_id, метка
        self.fired: dict[int, int] = {}
        self.scenarios_path = scenarios_path
        self.manifest_path = manifest_path

    def rules(self) -> list[dict[str, Any]]:
        try:
            with open(self.scenarios_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return []
        except ValueError as error:
            log("scenarios_invalid", error=str(error))
            return []
        rules = data.get("rules", []) if isinstance(data, dict) else []
        return [rule for rule in rules if isinstance(rule, dict)]

    def sample_marks(self) -> dict[str, int]:
        """Имя образца → (text_seed << 11 | chat_seed) из manifest.json генератора."""
        try:
            with open(self.manifest_path, encoding="utf-8") as handle:
                items = json.load(handle)
        except (FileNotFoundError, ValueError):
            return {}
        marks: dict[str, int] = {}
        for item in items if isinstance(items, list) else []:
            if item.get("text_seed") is not None:
                marks[str(item["name"])] = (int(item["text_seed"]) << 11) | int(item["chat_seed"])
        return marks


STATE: State


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"ts": datetime.now(UTC).isoformat(timespec="seconds"), "event": event, **fields}), flush=True)


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _image_bytes(body: dict[str, Any]) -> bytes:
    for message in body.get("messages", []):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if part.get("type") == "image_url":
                url = part["image_url"]["url"]
                return base64.b64decode(url.split(",", 1)[1])
    return b""


def _matches(match: dict[str, Any], ctx: dict[str, Any], marks: dict[str, int]) -> bool:
    for key, expected in match.items():
        if key == "sample":
            if ctx["mark_key"] is None or marks.get(str(expected)) != ctx["mark_key"]:
                return False
        elif ctx.get(key) != expected:
            return False
    return True


def respond(body: dict[str, Any]) -> tuple[int, dict[str, Any] | None, str | None]:
    """(HTTP-статус, тело-ответ модели или None, особая ошибка: timeout/invalid_json/empty)."""
    schema = (body.get("response_format") or {}).get("json_schema", {}).get("name", "")
    kind = "address_bar" if schema == "AddressBarAnswerV1" else "frame"
    data = _image_bytes(body)
    sha = hashlib.sha256(data).hexdigest()
    mark = None
    size = (0, 0)
    signature: list[int] | None = None
    if data:
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                image.load()
                size = image.size
                signature = top_signature(image, crop=kind == "address_bar")
                if kind == "frame":
                    mark = decode_mark(image)
        except Exception as error:  # картинка не открылась — работаем по sha256
            log("image_unreadable", error=str(error)[:200])
    with STATE.lock:
        STATE.requests += 1
        number = STATE.requests
        last = None
        if kind == "address_bar" and signature is not None and STATE.frames:
            last = min(STATE.frames, key=lambda frame: signature_distance(frame["signature"], signature))
    if mark is not None:
        text_seed, chat_seed, mode = mark
    else:
        text_seed, chat_seed, mode = int(sha[:6], 16) % 2048, int(sha[6:12], 16) % 2048, "normal"
    ctx = {
        "request": number,
        "kind": kind,
        "sha256": sha,
        "mark_key": (text_seed << 11 | chat_seed) if mark is not None else None,
    }
    if kind == "address_bar" and last is not None:
        ctx["mark_key"] = last.get("mark_key")

    rule = None
    marks = STATE.sample_marks()
    for index, candidate in enumerate(STATE.rules()):
        if not _matches(candidate.get("match", {}), ctx, marks):
            continue
        times = candidate.get("times")
        with STATE.lock:
            fired = STATE.fired.get(index, 0)
            if times is not None and fired >= int(times):
                continue
            STATE.fired[index] = fired + 1
        rule = candidate
        break

    log(
        "request",
        n=number,
        kind=kind,
        sha256=sha,
        size=list(size),
        mark=None if mark is None else {"text_seed": text_seed, "chat_seed": chat_seed, "mode": mode},
        rule=None if rule is None else rule.get("match"),
    )

    error = (rule or {}).get("error")
    if error is None and kind == "frame" and mode == "fail":
        error = 500
    if error is None and kind == "frame" and mode == "invalid_json":
        error = "invalid_json"
    if isinstance(error, int):
        return error, None, None
    if error in {"timeout", "invalid_json", "empty"}:
        return 200, None, str(error)

    if kind == "address_bar":
        if rule is not None and "answer" in rule:
            answer = rule["answer"]
        elif last is not None:
            answer = {"url": last["url"], "chat_id": last["chat_id"]}
        else:
            answer = {"url": None, "chat_id": None}
        if rule is not None and "patch" in rule:
            answer = deep_merge(answer, rule["patch"])
        return 200, answer, None

    width, height = size
    layout = "mobile_app" if height and width / height < 0.8 else "desktop_window"
    f = facts(text_seed, chat_seed)
    answer = screenshot_answer(f, layout, edited=mode == "edited")
    if rule is not None and "answer" in rule:
        answer = rule["answer"]
    if rule is not None and "patch" in rule:
        answer = deep_merge(answer, rule["patch"])
    if signature is not None:
        with STATE.lock:
            STATE.frames = [frame for frame in STATE.frames if frame["sha256"] != sha][-31:]
            STATE.frames.append(
                {
                    "sha256": sha,
                    "signature": signature,
                    "url": (answer.get("url") or {}).get("value"),
                    "chat_id": (answer.get("chat_id") or {}).get("value"),
                    "mark_key": ctx["mark_key"],
                }
            )
    return 200, answer, None


class Handler(BaseHTTPRequestHandler):
    server_version = "stand-fake-vlm/1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — сигнатура базового класса
        return

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/health", "/v1/models"}:
            self._send(200, {"status": "ok", "requests": STATE.requests})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length))
            status, answer, special = respond(body)
        except Exception as error:
            log("handler_failed", error=str(error)[:300])
            self._send(500, {"error": "fake failed"})
            return
        if status != 200:
            self._send(status, {"error": {"message": f"stand scenario: HTTP {status}"}})
            return
        if special == "timeout":
            time.sleep(float(os.environ.get("FAKE_VLM_TIMEOUT_SLEEP", "75")))
            self._send(504, {"error": "timeout"})
            return
        if special == "invalid_json":
            content = "Извините, не могу прочитать скриншот."
        elif special == "empty":
            content = ""
        else:
            content = json.dumps(answer, ensure_ascii=False)
        self._send(
            200,
            {
                "id": f"stand-{STATE.requests}",
                "object": "chat.completion",
                "model": body.get("model") or "stand/fake-vlm",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1000, "completion_tokens": max(1, len(content) // 3)},
            },
        )


def main() -> None:
    global STATE
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=int(os.environ.get("FAKE_VLM_PORT", "8000")))
    here = os.path.dirname(os.path.abspath(__file__))
    parser.add_argument(
        "--scenarios", default=os.environ.get("FAKE_VLM_SCENARIOS", os.path.join(here, "scenarios.json"))
    )
    parser.add_argument(
        "--manifest", default=os.environ.get("FAKE_VLM_MANIFEST", os.path.join(here, "samples", "manifest.json"))
    )
    args = parser.parse_args()
    STATE = State(args.scenarios, args.manifest)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    log("listening", port=args.port, scenarios=args.scenarios)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
