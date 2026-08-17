from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.app.application import create_app
from backend.shared.settings import load_settings


@pytest.fixture
def app() -> FastAPI:
    return create_app(load_settings("backend/shared/settings/config.test.yaml"))


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
