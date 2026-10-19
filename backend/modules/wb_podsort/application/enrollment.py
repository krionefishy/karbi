from backend.modules.wb_core.application import TrackedEnrollment
from backend.modules.wb_podsort.infrastructure.postgres import PodsortRepository

AUTOMATION_ID = "wb-podsort"
TITLE = "Подсорт WB"
DESCRIPTION = (
    "Сколько везти на склады WB по регионам: продажи за 7 или 14 дней на срок покрытия минус то, что уже "
    "лежит на складах WB региона. Все подключённые кабинеты в одной книге — сводный лист по регионам "
    "с ручными столбцами для логиста и склада, лист на кабинет."
)


class PodsortEnrollment(TrackedEnrollment[PodsortRepository]):
    automation_id = AUTOMATION_ID
    title = TITLE
