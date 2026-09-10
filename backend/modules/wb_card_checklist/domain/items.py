from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    key: str
    title: str
    meaning: str
    # Входит ли пункт в «готово». Справочный пункт показывает данные WB, но
    # правила «выполнено» у него нет.
    counted: bool = True


# Только то, что видно в данных WB: таблица ничего не просит отмечать руками.
# Названия — как в ручной таблице менеджеров, чтобы выгрузка читалась так же.
ITEMS: tuple[ChecklistItem, ...] = (
    ChecklistItem("description", "Описание (SEO)", "В карточке есть описание."),
    ChecklistItem(
        "characteristics",
        "Характеристики",
        "Сколько характеристик категории заполнено и какие пусты. В готовность не входит: правила полноты нет.",
        counted=False,
    ),
    ChecklistItem("photos", "Фото", "В карточке достаточно фото."),
    ChecklistItem("video", "Видео", "В карточке есть видео."),
    ChecklistItem("discount", "Скидка / СПП", "Скидка продавца выставлена. СПП WB не отдаёт."),
    ChecklistItem("reviews_present", "Отзывы есть", "На карточке есть отзывы."),
    ChecklistItem("reviews_with_photo", "Отзывы с фото", "На карточке есть отзывы с фото."),
    ChecklistItem("reviews_with_video", "Отзывы с видео", "На карточке есть отзывы с видео."),
)
ITEMS_BY_KEY = {item.key: item for item in ITEMS}
COUNTED_ITEMS = tuple(item for item in ITEMS if item.counted)
