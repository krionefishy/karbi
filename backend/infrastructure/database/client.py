from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class Database:
    def __init__(self, url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
        )
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    async def disconnect(self) -> None:
        await self._engine.dispose()

    async def ping(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session:
            yield session
