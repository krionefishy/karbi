import asyncio

from backend.shared.worker import WorkerApplication, run_application
from backend.workers.fin_reports.worker import FinReportsWorker


class FinReportsWorkerApplication(WorkerApplication[FinReportsWorker]):
    """Отдельный процесс финансовых отчётов: складывает дочитанные зеркалом отчёты реализации.

    В WB не ходит — читает зеркало `wb_core`, поэтому ни шлюз, ни ключи ему не нужны.
    """

    def build_worker(self) -> FinReportsWorker:
        return FinReportsWorker(self.database, self.settings)


if __name__ == "__main__":
    asyncio.run(run_application(FinReportsWorkerApplication))
