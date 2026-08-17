from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class Database:
    """Owns the shared SQLAlchemy engine; module repositories own all SQL."""

    def __init__(self) -> None:
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def connect(
        self,
        url: str,
        *,
        echo: bool = False,
        pool_size: int = 5,
        max_overflow: int = 5,
    ) -> None:
        if self._engine is not None:
            return
        self._engine = create_async_engine(
            url,
            echo=echo,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def disconnect(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None
        self._session_factory = None

    def session(self) -> AsyncSession:
        if self._session_factory is None:
            raise RuntimeError("Database is not connected")
        return self._session_factory()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def ping(self) -> bool:
        if self._engine is None:
            return False
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
