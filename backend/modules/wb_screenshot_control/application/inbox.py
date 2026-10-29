import uuid
from typing import Any

from pydantic import BaseModel, ValidationError


class InvalidEventError(Exception):
    """Сообщение не разобрать никогда: повтор только заблокировал бы партицию."""


def parse_event[E: BaseModel](model: type[E], payload: Any) -> tuple[E, uuid.UUID]:
    """Событие и его `event_id` (его дописывает outbox); без `event_id` повтор не отсечь — такое не принимаем."""
    try:
        event = model.model_validate(payload)
    except ValidationError as error:
        raise InvalidEventError(str(error)) from error
    event_id = getattr(event, "event_id", None)
    if event_id is None:
        raise InvalidEventError("event_id is missing")
    return event, event_id
