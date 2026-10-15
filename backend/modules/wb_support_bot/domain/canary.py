"""Канареечный выпуск: живая отправка по доле диалогов.

Доля выбирается по устойчивому хешу диалога, а не случайно на каждом проходе:
один и тот же диалог остаётся в канарейке (или вне её) всё время, поэтому
покупатель не получает сообщения «то да, то нет», а при росте процента в
канарейке остаются те же диалоги плюс новые.
"""

import hashlib
from datetime import datetime


def canary_bucket(chat_id: str, prompt_at: datetime) -> int:
    """Номер корзины диалога от 0 до 99."""
    digest = hashlib.sha256(f"{chat_id}|{prompt_at.isoformat()}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % 100


def in_canary(chat_id: str, prompt_at: datetime, percent: int) -> bool:
    return canary_bucket(chat_id, prompt_at) < percent
