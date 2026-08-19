import asyncio

from backend.workers.notifications.application import run_worker

if __name__ == "__main__":
    asyncio.run(run_worker())
