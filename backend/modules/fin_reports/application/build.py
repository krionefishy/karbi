"""Сборка отчёта: дочитанные зеркалом отчёты реализации складываются в суммы модуля.

Отчёт WB неизменяем — складывается один раз. Страница и выгрузка после
этого читают сотни готовых сумм, а зеркало с миллионами строк трогает только
воркер, по одному отчёту за запрос.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from backend.modules.fin_reports.infrastructure.postgres import FinReportsRepository, OzonDayMark
from backend.modules.wb_core.application import OzonAccrualMirror, SalesReportMirror
from backend.storage.pg import Database

# Версия правила сложения. Меняется, когда в суммах появляется новое поле или
# ключ: отчёты, сложенные по старой версии, пересобираются сами.
FACTS_VERSION = 1
# Отдельная версия для начислений Ozon: их правило сложения своё.
# 2 — продажи и возвраты одного SKU за день складываются порознь.
OZON_FACTS_VERSION = 2
# С какого дня отчёты вообще интересны: раньше зеркало не хранит.
HISTORY_FROM = date(2024, 1, 1)
# Окно в будущее — на отчёт, период которого зеркало записало с завтрашней датой.
HISTORY_AHEAD = timedelta(days=31)


logger = logging.getLogger("fin_reports.build")


@dataclass(frozen=True, slots=True)
class BuildOutcome:
    built: int
    # Сколько дочитанных отчётов ещё ждёт сложения: потолок за проход не дал дойти.
    left: int
    # Отчёты, на которых сложение упало: пропущены, остальные сложены; в `left` не входят.
    failed: int = 0


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
        failed = 0
        for report in due[:limit]:
            self._heartbeat()
            # Отчёт — своей транзакцией: сбой на десятом не откатывает девять сложенных
            # и не запирает одиннадцатый — старший отчёт в очереди стоит первым всегда.
            try:
                async with self.database.session() as session:
                    totals = await SalesReportMirror(session).totals(seller_id, [report.report_id])
                    await FinReportsRepository(session).save_report_facts(
                        seller_id, report, totals, version=FACTS_VERSION, now=stamp
                    )
                    await session.commit()
            except Exception:
                failed += 1
                logger.exception("fin_reports_report_build_failed seller=%s report=%s", seller_id, report.report_id)
        return BuildOutcome(built=min(len(due), limit) - failed, left=max(len(due) - limit, 0), failed=failed)

    async def build_ozon(self, seller_id: uuid.UUID, *, limit: int, now: datetime | None = None) -> BuildOutcome:
        """Дни начислений Ozon, которых суммы модуля не видели или видели другими.

        День Ozon, в отличие от отчёта WB, не окончателен: зеркало перечитывает
        последние дни (Ozon дописывает начисления задним числом) и переносит
        начисление на другой день, когда Ozon правит дату. Поэтому сложенный день
        помнит отпечаток зеркала — число строк и время чтения — и складывается
        заново, когда отпечаток сменился.
        """
        stamp = now or datetime.now(UTC)
        async with self.database.session() as session:
            mirror = OzonAccrualMirror(session)
            through = await mirror.collected_through(seller_id)
            signatures = await mirror.day_signatures(seller_id)
            built = await FinReportsRepository(session).built_ozon_days(seller_id)
        if through is None or not signatures:
            return BuildOutcome(built=0, left=0)

        def mark_of(day: date) -> OzonDayMark:
            lines, collected_at = signatures.get(day, (0, None))
            return OzonDayMark(OZON_FACTS_VERSION, lines, collected_at)

        # Дни от первого начисления до курсора зеркала; пустой день тоже складывается — нулём.
        first = min(min(signatures), *built) if built else min(signatures)
        due = [
            day
            for day in (first + timedelta(days=offset) for offset in range((through - first).days + 1))
            if built.get(day) != mark_of(day)
        ]
        failed = 0
        for day in due[:limit]:
            self._heartbeat()
            try:
                async with self.database.session() as session:
                    lines = await OzonAccrualMirror(session).lines(seller_id, since=day, until=day)
                    await FinReportsRepository(session).save_ozon_day(
                        seller_id, day, lines, mark=mark_of(day), now=stamp
                    )
                    await session.commit()
            except Exception:
                failed += 1
                logger.exception("fin_reports_ozon_day_build_failed seller=%s day=%s", seller_id, day)
        return BuildOutcome(built=min(len(due), limit) - failed, left=max(len(due) - limit, 0), failed=failed)
