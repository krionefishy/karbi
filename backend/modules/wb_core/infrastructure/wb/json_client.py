import logging
from typing import Any

from backend.modules.wb_core.infrastructure.wb.egress import EgressGateway


class WBJsonClient:
    """Общий вход для JSON-клиентов автоматизаций: тонкая обёртка над шлюзом.

    Троттлинг, ретраи и подстановка ключа живут на шлюзе wb-egress; здесь
    остаётся только имя раздела WB (`bucket`) и человеческие тексты ошибок.
    """

    bucket = "statistics"
    api_name = "WB API"
    # Category of the WB token this client needs. A key is issued per category,
    # so "недействителен" is the wrong thing to tell someone whose key simply
    # lacks one checkbox.
    category = ""

    def __init__(self, gateway: EgressGateway, *, priority: str = "background") -> None:
        self.gateway = gateway
        # interactive — для действий оператора: в очереди шлюза они обгоняют
        # фоновые автоматизации, а не стоят за ночным синком.
        self.priority = priority
        self.logger = logging.getLogger(f"wb.{self.bucket}.client")

    async def request(
        self,
        method: str,
        path: str,
        seller_id: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        return await self.gateway.call(
            seller_id=seller_id,
            api=self.bucket,
            method=method,
            path=path,
            params=params,
            json=json,
            priority=self.priority,
            api_name=self.api_name,
            category=self.category,
        )
