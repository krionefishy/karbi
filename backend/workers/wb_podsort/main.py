import asyncio

from backend.workers.wb_podsort.application import run_worker

if __name__ == "__main__":
    asyncio.run(run_worker())
