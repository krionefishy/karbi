import re

TOKEN_PREFIX = "mas_"
# Crockford base32 без I, L, O, U: код переписывают с экрана руками, путаницы 0/O и 1/I быть не должно.
PAIRING_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PAIRING_CODE_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{8}$"

_VERSION = re.compile(r"^(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")


def parse_version(value: str) -> tuple[int, int, int] | None:
    match = _VERSION.fullmatch(value.strip())
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def version_supported(version: str | None, minimum: str) -> bool:
    """Версия без заголовка или нечитаемая — устаревшая: так старое расширение получит 426, а не 500."""
    current = parse_version(version or "")
    required = parse_version(minimum)
    if required is None:
        raise ValueError(f"Invalid minimum version: {minimum!r}")
    return current is not None and current >= required
