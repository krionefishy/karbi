"""QR получения: подпись ссылки на картинку и сама картинка.

Сайт покупателя рисует QR из строки на устройстве; мы делаем то же — строка
лежит у нас, картинка нужна боту. Ссылка на картинку подписана HMAC от
кабинета и даты: адрес нельзя угадать, а сама строка QR в него не попадает.
"""

import hashlib
import hmac
import io
import uuid
from datetime import date

import segno


def signature(secret: str, seller_id: uuid.UUID, day: date) -> str:
    message = f"returns-qr:{seller_id}:{day.isoformat()}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:40]


def verify(secret: str, seller_id: uuid.UUID, day: date, candidate: str) -> bool:
    return hmac.compare_digest(signature(secret, seller_id, day), candidate)


def render_png(text: str, *, scale: int = 10) -> bytes:
    """PNG с уровнем коррекции Q — как у сайта покупателя, сканеры ПВЗ к нему привыкли."""
    buffer = io.BytesIO()
    segno.make(text, error="q").save(buffer, kind="png", scale=scale, border=2)
    return buffer.getvalue()
