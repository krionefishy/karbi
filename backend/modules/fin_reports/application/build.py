"""Сборка отчёта: дочитанные зеркалом отчёты реализации складываются в суммы модуля.

Отчёт WB неизменяем — складывается один раз. Страница и выгрузка после
этого читают сотни готовых сумм, а зеркало с миллионами строк трогает только
воркер, по одному отчёту за запрос.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from backend.modules.fin_reports.infrastructure.postgres import FinReportsRepository
from backend.modules.wb_core.application import SalesReportMirror
from backend.storage.pg import Database

# Версия правила сложения. Меняется, когда в суммах появляется новое поле или
# ключ: отчёты, сложенные по старой версии, пересобираются сами.
FACTS_VERSION = 1
# С какого дня отчёты вообще интересны: раньше зеркало не хранит.
HISTORY_FROM = date(2024, 1, 1)
# Окно в будущее — на отчёт, период которого зеркало записало с завтрашней датой.
HISTORY_AHEAD = timedelta(days=31)


@dataclass(frozen=True, slots=True)
class BuildOutcome:
    built: int
    # Сколько дочитанных отчётов ещё ждёт сложения: потолок за проход не дал дойти.
    left: int


class FactsBuilder:
    """Складывает дочитанные отчёты кабинета, которых ещё нет в суммах модуля."""

    def __init__(self, database: Database, *, heartbeat: Callable[[], None] | None = None) -> None:
        self.database = database
        self._heartbeat = heartbeat or (lambda: None)

    async def build(self, seller_id: uuid.UUID, *, limit: int, now: datetime | None = None) -> BuildOutcome:
        stamp = now or datetime.now(UTC)
        async with self.database.session() as session:
            mirrored = await SalesReportMirror(session).reports(
                seller_id, since=HISTORY_FROM, until=stamp.date() + HISTORY_AHEAD
            )
            built = await FinReportsRepository(session).built_reports(seller_id)
        due = [item.report for item in mirrored if item.loaded and built.get(item.report.report_id) != FACTS_VERSION]
        for report in due[:limit]:
            self._heartbeat()
            # Отчёт — своей транзакцией: сбой на десятом не откатывает девять сложенных.
            async with self.database.session() as session:
                totals = await SalesReportMirror(session).totals(seller_id, [report.report_id])
                await FinReportsRepository(session).save_report_facts(
                    seller_id, report, totals, version=FACTS_VERSION, now=stamp
                )
                await session.commit()
        return BuildOutcome(built=min(len(due), limit), left=max(len(due) - limit, 0))
