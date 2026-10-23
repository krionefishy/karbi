import base64
import json
from typing import Any


class ServiceError(Exception):
    """Отказ, понятный клиенту. `code` — значение `ErrorCode` контракта; HTTP-статус выбирает presentation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def not_found() -> ServiceError:
    return ServiceError("not_found", "Скриншот не найден")


def encode_cursor(*values: Any) -> str:
    return base64.urlsafe_b64encode(json.dumps([str(value) for value in values]).encode()).decode().rstrip("=")


def decode_cursor(cursor: str, size: int) -> list[str]:
    """Курсор непрозрачен для клиента: что угодно, кроме выданного нами, — 422 `bad_cursor`."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        values = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise ServiceError("bad_cursor", "Курсор страницы не подходит, начните список заново") from error
    if not isinstance(values, list) or len(values) != size or not all(isinstance(item, str) for item in values):
        raise ServiceError("bad_cursor", "Курсор страницы не подходит, начните список заново")
    return values
