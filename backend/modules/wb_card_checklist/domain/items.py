from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    key: str
    title: str
    meaning: str
    counts_toward_ready: bool = True


ITEMS: tuple[ChecklistItem, ...] = (
    ChecklistItem("description", "Описание (SEO)", "Описание заполнено."),
    ChecklistItem(
        "characteristics",
        "Характеристики",
        "Сколько характеристик категории заполнено. Заполнять все не требуется — пустые видны в подсказке.",
    ),
    ChecklistItem("photos", "Фото", "Фото-контент установлен в карточку."),
    ChecklistItem("video", "Видео", "Видео установлено в карточку."),
    ChecklistItem(
        "documents",
        "Документы",
        "Документы (декларация, сертификат) загружены в карточку и приняты WB. "
        "Если категории они не нужны, пункт зелёный.",
    ),
    ChecklistItem("discount", "Скидка / СПП", "Скидка выставлена. СПП WB не отдаёт."),
    ChecklistItem("club_discount", "Скидка WB Клуба", "Для подписчиков WB Клуба выставлена скидка."),
    ChecklistItem("reviews_present", "Отзывы есть", "На карточке есть отзывы."),
    ChecklistItem("reviews_with_photo", "Отзывы с фото", "Есть фото-отзывы."),
    ChecklistItem("reviews_with_video", "Отзывы с видео", "Есть видео-отзывы."),
    ChecklistItem(
        "negative_recent",
        "Негатив в 5 свежих",
        "Среди пяти самых свежих отзывов нет оценок 1–3. Предупреждение: в «Готово» не входит, "
        "потому что правкой карточки отзыв не убрать.",
        counts_toward_ready=False,
    ),
)
ITEMS_BY_KEY = {item.key: item for item in ITEMS}
READY_ITEMS = tuple(item for item in ITEMS if item.counts_toward_ready)
