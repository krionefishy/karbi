import asyncio

from backend.workers.fin_reports.application import run_worker

if __name__ == "__main__":
    asyncio.run(run_worker())
