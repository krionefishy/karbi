import re
from dataclasses import dataclass

NO_CONTACT_PATTERNS: tuple[str, ...] = (
    r"не пиши\w*",
    r"не звони\w*",
    r"не беспокой\w*",
    r"не надо писать",
    r"хватит",
    r"прекрат\w*",
    r"перестан\w*",
    r"отстан\w*",
    r"отвал\w*",
    r"отпиш\w*",
    r"оставь\w* в покое",
    r"надоел\w*",
    r"надоело",
    r"достал\w*",
    r"заблокиру\w*",
    r"в черный список",
    r"в чс",
    r"отключите рассылку",
)

ATTENTION_PATTERNS: tuple[str, ...] = (
    r"жалоб\w*",
    r"жалуюсь",
    r"пожалуюсь",
    r"спам\w*",
)

_NO_CONTACT = re.compile(r"\b(?:" + "|".join(NO_CONTACT_PATTERNS) + r")\b")
_ATTENTION = re.compile(r"\b(?:" + "|".join(ATTENTION_PATTERNS) + r")\b")
_NOISE = re.compile(r"[^\w\s]+")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class StopMatch:
    no_contact: bool = False
    attention: bool = False


def normalize(text: str) -> str:
    """Нижний регистр, «ё» → «е», знаки препинания в пробелы."""
    lowered = text.lower().replace("ё", "е")
    return _SPACES.sub(" ", _NOISE.sub(" ", lowered)).strip()


def match(text: str | None) -> StopMatch:
    if not text:
        return StopMatch()
    normalized = normalize(text)
    return StopMatch(
        no_contact=_NO_CONTACT.search(normalized) is not None,
        attention=_ATTENTION.search(normalized) is not None,
    )
