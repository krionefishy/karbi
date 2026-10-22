import asyncio
import os
import shutil
import time
import uuid
from pathlib import Path, PurePosixPath


class StagingStore:
    """Каталог до выгрузки в S3, общий у api и воркера. Запись — tmp-файл и `os.replace`."""

    def __init__(self, root: str | Path, *, max_used_percent: int) -> None:
        self.root = Path(root)
        self.max_used_percent = max_used_percent

    def resolve(self, path: str) -> Path:
        relative = PurePosixPath(path)
        if not path or relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Staging path must be relative: {path!r}")
        return self.root.joinpath(*relative.parts)

    async def write(self, path: str, data: bytes) -> None:
        await asyncio.to_thread(self._write, self.resolve(path), data)

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
        """Каталог может ещё не существовать: тогда меряем ближайшего существующего предка."""
        probe = self.root
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        usage = shutil.disk_usage(probe)
        return usage.used * 100 < usage.total * self.max_used_percent

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
