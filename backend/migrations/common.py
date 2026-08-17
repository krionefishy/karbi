import asyncio
import os

from alembic import context
from sqlalchemy import MetaData, pool, text
from sqlalchemy.ext.asyncio import create_async_engine


def run_migrations(metadata: MetaData, schema: str) -> None:
    config = context.config
    database_url = os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))

    if context.is_offline_mode():
        context.configure(
            url=database_url,
            target_metadata=metadata,
            literal_binds=True,
            compare_type=True,
            include_schemas=True,
            version_table="alembic_version",
            version_table_schema=schema,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    async def run_online() -> None:
        engine = create_async_engine(database_url, poolclass=pool.NullPool)
        async with engine.connect() as connection:
            await connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            await connection.commit()

            def apply_migrations(sync_connection) -> None:
                context.configure(
                    connection=sync_connection,
                    target_metadata=metadata,
                    compare_type=True,
                    include_schemas=True,
                    version_table="alembic_version",
                    version_table_schema=schema,
                )
                with context.begin_transaction():
                    context.run_migrations()

            await connection.run_sync(apply_migrations)
        await engine.dispose()

    asyncio.run(run_online())
