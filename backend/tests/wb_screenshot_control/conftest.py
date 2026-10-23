from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from backend.tests.wb_screenshot_control.http_stand import Stand, open_stand


@pytest_asyncio.fixture
async def stand(tmp_path: Path) -> AsyncIterator[Stand]:
    async with open_stand(tmp_path) as opened:
        yield opened
