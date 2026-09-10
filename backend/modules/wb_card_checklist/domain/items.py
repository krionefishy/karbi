from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    key: str
    title: str
    meaning: str


# Только то, что видно в данных WB: таблица ничего не просит отмечать руками.
# Названия и смысл пунктов — из инструкции к ручной таблице менеджеров.
ITEMS: tuple[ChecklistItem, ...] = (
    ChecklistItem("description", "Описание (SEO)", "Описание заполнено."),
    ChecklistItem(
        "characteristics",
        "Характеристики",
        "Сколько характеристик категории заполнено. Заполнять все не требуется — пустые видны в подсказке.",
    ),
    ChecklistItem("photos", "Фото", "Фото-контент установлен в карточку."),
    ChecklistItem("video", "Видео", "Видео установлено в карточку."),
    ChecklistItem("discount", "Скидка / СПП", "Скидка выставлена. СПП WB не отдаёт."),
    ChecklistItem("club_discount", "Скидка WB Клуба", "Для подписчиков WB Клуба выставлена скидка."),
    ChecklistItem("reviews_present", "Отзывы есть", "На карточке есть отзывы."),
    ChecklistItem("reviews_with_photo", "Отзывы с фото", "Есть фото-отзывы."),
    ChecklistItem("reviews_with_video", "Отзывы с видео", "Есть видео-отзывы."),
)
ITEMS_BY_KEY = {item.key: item for item in ITEMS}
