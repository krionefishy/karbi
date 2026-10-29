import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_URL = re.compile(r"https?://\S+")
_LONG_NUMBER = re.compile(r"(?<![\w])\+?\d[\d\s().-]{7,}\d(?![\w])")
_LOGIN = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{4,}")

# Имена: полное ФИО или «Имя Отчество» узнаются по отчеству, поэтому «Верните Деньги» не задевается.
_PATRONYMIC = r"(?:ович|евич|ьич|ична|овна|евна|ьевна)"
_CAP = r"[А-ЯЁ][а-яё]+"
_FULL_NAME = re.compile(rf"\b{_CAP}\s+{_CAP}\s+{_CAP}{_PATRONYMIC}\b(?:\s+[А-ЯЁ]\.)?")
_NAME_PATRONYMIC = re.compile(rf"\b{_CAP}\s+{_CAP}{_PATRONYMIC}\b(?:\s+[А-ЯЁ]\.)?")
_SURNAME_INITIALS = re.compile(rf"\b{_CAP}\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.")
_INTRODUCES = re.compile(rf"(?i:меня\s+зовут|моё\s+имя|мое\s+имя)\s+{_CAP}")

# Адреса: улица, проспект и т. п. с названием и номерами дома, корпуса, квартиры; отдельно квартира и подъезд.
_STREET = re.compile(
    r"(?i:\b(?:ул|улица|пр-т|проспект|пер|переулок|бульвар|б-р|шоссе|наб|набережная)\.?\s+)"
    r"[А-ЯЁа-яё0-9\-]+"
    r"(?:[,\s]+(?:(?i:д|дом|кв|квартира|корп|корпус|к|стр|строение)\.?\s*)?\d+[А-Яа-я]?){0,4}"
)
_FLAT = re.compile(r"(?i:\b(?:кв|квартира|подъезд|под|этаж|эт)\.?\s*\d+)")


def mask_pii(text: str) -> str:
    """Заменяет почту, ссылки, идентификаторы, номера, логины, ФИО и адреса на заглушки. Смысл текста остаётся."""
    masked = _EMAIL.sub("<EMAIL>", text)
    masked = _UUID.sub("<ID>", masked)
    masked = _URL.sub("<URL>", masked)
    masked = _LONG_NUMBER.sub("<NUM>", masked)
    masked = _LOGIN.sub("<LOGIN>", masked)
    masked = _FULL_NAME.sub("<NAME>", masked)
    masked = _NAME_PATRONYMIC.sub("<NAME>", masked)
    masked = _SURNAME_INITIALS.sub("<NAME>", masked)
    masked = _INTRODUCES.sub("меня зовут <NAME>", masked)
    masked = _STREET.sub("<ADDRESS>", masked)
    return _FLAT.sub("<ADDRESS>", masked)
