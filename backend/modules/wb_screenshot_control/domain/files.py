import re

KEY_ROOT = "dbs-screenshots"
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def sniff_image_type(head: bytes) -> str | None:
    """Тип по магическим байтам: заголовку Content-Type от клиента не верим."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def object_key(sha256: str, kind: str, content_type: str = "image/jpeg") -> str:
    """Ключ по содержимому: повторная выгрузка того же файла кладёт его на то же место."""
    if not _SHA256.fullmatch(sha256):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    if kind == "orig":
        return f"{KEY_ROOT}/orig/{sha256[:2]}/{sha256}.{_EXTENSIONS[content_type]}"
    if kind == "thumb":
        return f"{KEY_ROOT}/thumb/{sha256[:2]}/{sha256}.jpg"
    raise ValueError(f"Unknown storage kind: {kind!r}")
