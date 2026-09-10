from dataclasses import dataclass
from enum import StrEnum


class ItemKind(StrEnum):
    # Решает API: менеджер отметку не ставит и не снимает.
    AUTO = "auto"
    # API видит факт, качество подтверждает менеджер. Без факта отметку не поставить.
    CONFIRM = "confirm"
    # API этого не видит вовсе — только отметка менеджера.
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    key: str
    title: str
    kind: ItemKind
    meaning: str


# Порядок и названия — как в таблице, которую менеджеры вели руками: выгрузка
# ложится на старые файлы колонка в колонку.
ITEMS: tuple[ChecklistItem, ...] = (
    ChecklistItem(
        "description",
        "Описание (SEO)",
        ItemKind.CONFIRM,
        "Описание заполнено и оптимизировано под поисковые запросы.",
    ),
    ChecklistItem(
        "characteristics",
        "Характеристики",
        ItemKind.AUTO,
        "Заполнены все характеристики, доступные в категории.",
    ),
    ChecklistItem("photos", "Фото", ItemKind.AUTO, "Фото-контент установлен в карточку."),
    ChecklistItem("video", "Видео", ItemKind.AUTO, "Видео установлено в карточку."),
    ChecklistItem("video_cover", "Видеообложка", ItemKind.MANUAL, "Видеообложка установлена."),
    ChecklistItem("rich_content", "Рич-контент", ItemKind.MANUAL, "Рич-контент собран и опубликован."),
    ChecklistItem("cashback", "Кэшбек", ItemKind.MANUAL, "На товар настроен кэшбек."),
    ChecklistItem("discount", "Скидка / СПП", ItemKind.CONFIRM, "Скидка выставлена, цена на витрине рыночная."),
    ChecklistItem(
        "reviews_for_points",
        "Отзывы за баллы",
        ItemKind.MANUAL,
        "Товар подключён к программе «Отзывы за баллы».",
    ),
    ChecklistItem(
        "reviews_present",
        "Отзывы есть",
        ItemKind.AUTO,
        "На карточке есть отзывы (завалы после раздачи).",
    ),
    ChecklistItem(
        "reviews_with_photo",
        "Отзывы с фото",
        ItemKind.CONFIRM,
        "Есть фото-отзывы, и на них раскрыта суть товара.",
    ),
    ChecklistItem(
        "reviews_with_video",
        "Отзывы с видео",
        ItemKind.CONFIRM,
        "Есть видео-отзывы, и на них раскрыта суть товара.",
    ),
    ChecklistItem(
        "ratings_linked",
        "Оценки прицеплены",
        ItemKind.MANUAL,
        "Оценки из кабинета прицеплены к основному товару.",
    ),
)
ITEMS_BY_KEY = {item.key: item for item in ITEMS}
