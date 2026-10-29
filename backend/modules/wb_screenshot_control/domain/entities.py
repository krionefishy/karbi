"""Словари значений модуля: на них опираются ограничения таблиц, события и правила.

HTTP-контракт держит те же значения закрытыми `Literal` в `presentation/http/schemas.py`;
расхождение ловит тест, поэтому новое значение добавляется в оба места.
"""

OUTCOMES = ("positive", "amended", "deleted")
OUTCOME_SOURCES = ("button", "caption", "form", "admin")
VERDICTS = ("accepted", "duplicate", "needs_review", "rejected")
CHANNELS = ("extension", "telegram", "web")
STAGES = ("processing", "extracted", "decided")
MODES = ("off", "shadow", "enforce")

STAGE_PROCESSING, STAGE_EXTRACTED, STAGE_DECIDED = STAGES

# Подписи, которыми менеджеры пишут исход; форма и кнопки показывают их же.
OUTCOME_CAPTIONS = {"positive": "полож", "amended": "доп", "deleted": "удален"}
OUTCOME_LABELS = {
    "positive": "Отзыв положительный",
    "amended": "Отзыв изменён на положительный",
    "deleted": "Отзыв удалён",
}

IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")
EXTRACTION_STATUSES = ("ok", "failed")
LAYOUTS = ("desktop_window", "desktop_viewport", "mobile_app", "other", "unreadable")
CASE_KINDS = ("desktop", "mobile")
CHAT_ID_SOURCES = ("url", "image")
TIME_PRECISIONS = ("minute", "day")

STORAGE_KINDS = ("orig", "thumb")
STORAGE_STATES = ("staged", "uploading", "stored", "delete_requested", "deleted", "failed")
