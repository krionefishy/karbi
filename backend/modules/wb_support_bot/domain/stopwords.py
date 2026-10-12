"""Стоп-слова в сообщениях покупателя.

Любое сообщение покупателя и так останавливает рассылку. Стоп-слова нужны,
чтобы специалист увидел в карточке флаг: человек просит не писать или
жалуется, и отвечать ему по шаблону не стоит.

Сравнение без регистра, «ё» = «е», по границам слов: «суд» не должен
срабатывать на «судя».
"""

import re
from dataclasses import dataclass

# Просьба не писать: бот больше не пишет в этот диалог.
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

# Жалобы: диалог получает метку «требует внимания», бот рассылку не останавливает
# сверх того, что уже сделал ответ покупателя.
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
