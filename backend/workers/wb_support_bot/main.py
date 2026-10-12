import asyncio

from backend.workers.wb_support_bot.application import run_worker

if __name__ == "__main__":
    asyncio.run(run_worker())
