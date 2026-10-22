"""Порты модуля: распознавание, решение и хранилища байтов. Реализации подключает `infrastructure/wiring.py`."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from backend.modules.wb_screenshot_control.domain.pipeline_types import (
    Candidate,
    ClientHints,
    Decision,
    DecisionInput,
    ExtractionResult,
)


class Extractor(Protocol):
    """Картинка → нормализованные поля. Исключение или `status="failed"` — неудачная попытка, конвейер повторит."""

    async def extract(
        self, image: bytes, *, channel: str, hints: ClientHints, received_at: datetime
    ) -> ExtractionResult: ...


class Decider(Protocol):
    """Чистое решение по скрину и кандидатам; в базу и сеть не ходит."""

    def decide(self, inp: DecisionInput, candidates: Sequence[Candidate]) -> Decision: ...


class Staging(Protocol):
    """Временный том до выгрузки в S3. Пути относительные, запись атомарна."""

    async def write(self, path: str, data: bytes) -> None: ...

    async def read(self, path: str) -> bytes:
        """`FileNotFoundError`, если файла нет."""
        ...

    async def delete(self, path: str) -> None: ...

    async def exists(self, path: str) -> bool: ...

    def has_room(self) -> bool: ...

    async def older_than(self, seconds: float) -> list[tuple[str, float]]:
        """Файлы старше `seconds`: относительный путь и возраст в секундах."""
        ...


class BlobStore(Protocol):
    """Объектное хранилище одного бакета. Пишут и удаляют только подписчики Kafka."""

    @property
    def bucket(self) -> str: ...

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def size(self, key: str) -> int | None:
        """Размер объекта или `None`, если его нет."""
        ...

    async def delete(self, key: str) -> None: ...

    def presigned_get(self, key: str, *, expires_in: int, content_type: str, attachment: bool) -> str: ...
