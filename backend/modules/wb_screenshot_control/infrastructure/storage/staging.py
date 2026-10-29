import asyncio
import os
import shutil
import threading
import time
import uuid
from pathlib import Path, PurePosixPath

SIZE_TTL_SECONDS = 30.0
"""Размер каталога пересчитывается обходом не чаще раза в 30 секунд; между обходами к нему
прибавляется записанное этим процессом."""

# Кэш размера по корню: StagingStore создаётся на каждый запрос приёма, а обход каталога не бесплатен.
_SIZES: dict[Path, tuple[int, float]] = {}
_SIZES_LOCK = threading.Lock()


class StagingStore:
    """Каталог до выгрузки в S3, общий у api и воркера. Запись — tmp-файл и `os.replace`.

    Места два предела: заполненность тома (`max_used_percent`, том общий с базой) и абсолютный
    размер самого каталога (`max_bytes`). Без S3 файлы копятся здесь, и предел каталога не даёт
    им съесть место базы.
    """

    def __init__(self, root: str | Path, *, max_used_percent: int, max_bytes: int | None = None) -> None:
        self.root = Path(root)
        self.max_used_percent = max_used_percent
        self.max_bytes = max_bytes

    def resolve(self, path: str) -> Path:
        relative = PurePosixPath(path)
        if not path or relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Staging path must be relative: {path!r}")
        return self.root.joinpath(*relative.parts)

    async def write(self, path: str, data: bytes) -> None:
        await asyncio.to_thread(self._write, self.resolve(path), data)
        with _SIZES_LOCK:
            cached = _SIZES.get(self.root)
            if cached is not None:
                _SIZES[self.root] = (cached[0] + len(data), cached[1])

    @staticmethod
    def _write(target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temporary, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    async def read(self, path: str) -> bytes:
        return await asyncio.to_thread(self.resolve(path).read_bytes)

    async def delete(self, path: str) -> None:
        await asyncio.to_thread(self.resolve(path).unlink, missing_ok=True)

    async def exists(self, path: str) -> bool:
        return await asyncio.to_thread(self.resolve(path).is_file)

    def has_room(self) -> bool:
        """Есть ли место: том заполнен меньше `max_used_percent` и каталог меньше `max_bytes`.

        Синхронный (обход каталога раз в `SIZE_TTL_SECONDS`): вызывающий в event loop зовёт его через
        `asyncio.to_thread`.
        """
        usage = self._disk_usage()
        if usage is not None and usage[0] * 100 >= usage[1] * self.max_used_percent:
            return False
        return self.max_bytes is None or self.used_bytes() < self.max_bytes

    def used_percent(self) -> float | None:
        """Насколько заполнено: большее из «каталог / max_bytes» и «занято на томе», в процентах."""
        shares: list[float] = []
        usage = self._disk_usage()
        if usage is not None and usage[1]:
            shares.append(usage[0] * 100 / usage[1])
        if self.max_bytes:
            shares.append(self.used_bytes() * 100 / self.max_bytes)
        return round(max(shares), 1) if shares else None

    def used_bytes(self) -> int:
        """Размер каталога, с кэшем на `SIZE_TTL_SECONDS`."""
        now = time.monotonic()
        with _SIZES_LOCK:
            cached = _SIZES.get(self.root)
            if cached is not None and now - cached[1] < SIZE_TTL_SECONDS:
                return cached[0]
        size = self._measure()
        with _SIZES_LOCK:
            _SIZES[self.root] = (size, now)
        return size

    def _measure(self) -> int:
        if not self.root.exists():
            return 0
        total = 0
        for file in self.root.rglob("*"):
            try:
                if file.is_file():
                    total += file.stat().st_size
            except OSError:
                continue  # файл удалили между обходом и stat
        return total

    def _disk_usage(self) -> tuple[int, int] | None:
        """Занято и всего на томе. Каталога может ещё не быть: тогда меряем ближайшего предка."""
        probe = self.root
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            usage = shutil.disk_usage(probe)
        except OSError:
            return None
        return usage.used, usage.total

    async def older_than(self, seconds: float) -> list[tuple[str, float]]:
        """Файлы старше `seconds`: относительный путь и возраст. Недописанные tmp тоже сюда попадают."""
        return await asyncio.to_thread(self._older_than, seconds)

    def _older_than(self, seconds: float) -> list[tuple[str, float]]:
        if not self.root.exists():
            return []
        now = time.time()
        found: list[tuple[str, float]] = []
        for file in self.root.rglob("*"):
            if not file.is_file():
                continue
            age = now - file.stat().st_mtime
            if age >= seconds:
                found.append((file.relative_to(self.root).as_posix(), age))
        return found
