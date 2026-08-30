"""Клиент шлюза wb-egress — единственная дорога отсюда к Wildberries.

Ключи селлеров, троттлинг и исходящие IP живут на шлюзе: этот сервис передаёт
`seller_id`, шлюз сам подставляет ключ и выпускает запрос с закреплённого за
селлером адреса. Прямых обращений к `*.wildberries.ru` в коде больше нет.

Канал — тот же набор, что у телеграм-релея: HTTPS с запиненным self-signed
сертификатом шлюза и короткоживущий JWT (HS256) на каждый запрос.
"""

import asyncio
import random
import ssl
import time
import uuid
from typing import Any

import httpx
import jwt

from backend.shared.settings import EgressConfig

ATTEMPTS = 3


class WBPermanentError(Exception):
    """Повтор не поможет: ключ, права, несуществующий метод или сам запрос."""


class WBTemporaryError(Exception):
    """Стоит повторить позже: лимиты, сеть, недоступность WB или шлюза."""


class EgressAdminError(Exception):
    """Управляющий вызов шлюза не прошёл; текст — для статуса селлера."""

    def __init__(self, message: str, *, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class EgressGateway:
    """Один клиент на процесс: и WB-запросы, и управление селлерами."""

    def __init__(self, config: EgressConfig) -> None:
        self._config = config
        self._verify: str | bool | ssl.SSLContext = True
        lowered = config.verify.lower()
        if lowered in {"true", "1", "yes"}:
            self._verify = True
        elif lowered in {"false", "0", "no"}:
            self._verify = False
        else:
            # Путь к сертификату шлюза. Соединение идёт на VPS по IP, а имя в
            # сертификате — внутреннее, поэтому проверка имени выключена:
            # доверие держится на пиннинге самого сертификата.
            context = ssl.create_default_context(cafile=config.verify)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_REQUIRED
            self._verify = context
        self._timeout = httpx.Timeout(float(config.request_timeout_seconds), connect=10.0)
        self._client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- WB-запросы -----------------------------------------------------

    async def call(
        self,
        *,
        seller_id: str,
        api: str,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        priority: str = "background",
        api_name: str = "WB API",
        category: str = "",
    ) -> Any:
        """Тело ответа WB, либо та же пара ошибок, что была у прямых клиентов.

        Локальные повторы (ATTEMPTS) существуют только для транспортных сбоев
        до шлюза. Всё, что шлюз донёс как ответ — включая 429 и 5xx от WB, —
        окончательно: шлюз уже отработал свои ретраи и своё ожидание в очереди,
        повторять поверх — значит множить нагрузку на задыхающийся эндпоинт.
        Дальше этим занимаются повторы уровня задач.
        """
        last_error = ""
        for attempt in range(ATTEMPTS):
            payload = await self._send(
                seller_id=seller_id, api=api, method=method, path=path, params=params, json=json, priority=priority
            )
            if isinstance(payload, _Retry):
                last_error = payload.reason
                if attempt < ATTEMPTS - 1:
                    await asyncio.sleep(payload.delay + random.uniform(0.1, 0.5))
                    continue
                raise WBTemporaryError(f"{api_name}: {last_error}")
            status = int(payload.get("status", 0))
            if status in {401, 403}:
                raise WBPermanentError(self._access_error(api_name, category))
            if status == 404:
                raise WBPermanentError(f"{api_name} не знает метод {path}")
            if status == 429 or status >= 500:
                raise WBTemporaryError(f"{api_name} отвечает HTTP {status}")
            if status >= 400:
                raise WBPermanentError(f"{api_name} отклонил запрос: HTTP {status}")
            if payload.get("truncated"):
                # Обрезанный JSON перестаёт быть JSON: молча отдать его дальше
                # значит записать в базу половину страницы как целую.
                raise WBTemporaryError(
                    f"{api_name}: ответ не поместился в потолок шлюза и пришёл обрезанным — "
                    "поднимите EGRESS_MAX_RESPONSE_BYTES или уменьшите размер страницы"
                )
            return payload.get("body")
        raise WBTemporaryError(f"{api_name}: {last_error or 'запрос не прошёл'}")

    async def _send(
        self,
        *,
        seller_id: str,
        api: str,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        json: Any | None,
        priority: str,
    ) -> "dict[str, Any] | _Retry":
        client = self._require_client()
        try:
            response = await client.post(
                "/api/v1/wb/request",
                json={
                    "seller_id": seller_id,
                    "api": api,
                    "method": method,
                    "path": path,
                    "query": params,
                    "body": json,
                    "priority": priority,
                },
                headers=self._headers(),
            )
        except httpx.RequestError as error:
            return _Retry(reason=f"шлюз WB недоступен: {error}", delay=2.0)
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                # Обрезанный ответ или HTML от промежуточного nginx — не повод
                # ронять вызывающего сырым исключением мимо контракта ошибок.
                return _Retry(reason="шлюз вернул не JSON", delay=2.0)
            if not isinstance(payload, dict):
                return _Retry(reason="шлюз вернул JSON неожиданной формы", delay=2.0)
            return payload
        detail = self._detail(response)
        if response.status_code == 429:
            # Очередь шлюза не нашла бюджета даже за своё ожидание — дальше
            # пусть решает повтор уровня задачи, а не сон внутри вызова.
            raise WBTemporaryError(detail or "Лимит запросов к WB исчерпан — повторите позже")
        if response.status_code in {404, 409}:
            raise WBPermanentError(detail or f"Селлер {seller_id} не обслуживается шлюзом")
        if response.status_code in {400, 422}:
            raise WBPermanentError(detail or "Шлюз отверг запрос как некорректный")
        if response.status_code == 401:
            # Рассинхрон секрета — постоянная ошибка конфигурации: падать надо
            # сразу и громко, а не жечь минуту сна на каждый вызов.
            raise WBTemporaryError("Шлюз WB отверг авторизацию сервиса — проверьте EGRESS_JWT_SECRET на обеих сторонах")
        # Текст шлюза дороже кода ответа: в нём написано, почему он не смог —
        # например, что не удалось привязаться к исходящему адресу селлера.
        reason = f"шлюз ответил HTTP {response.status_code}"
        return _Retry(reason=f"{reason}: {detail}" if detail else reason, delay=5.0)

    # --- Управление селлерами -------------------------------------------

    async def put_seller(self, *, seller_id: str, name: str, api_key: str, event_version: int) -> dict[str, Any]:
        return await self._admin(
            "PUT", f"/api/v1/sellers/{seller_id}", {"name": name, "api_key": api_key, "event_version": event_version}
        )

    async def rename_seller(self, *, seller_id: str, name: str, event_version: int) -> dict[str, Any]:
        payload = {"name": name, "event_version": event_version}
        return await self._admin("PATCH", f"/api/v1/sellers/{seller_id}", payload)

    async def disable_seller(self, *, seller_id: str, event_version: int) -> dict[str, Any]:
        return await self._admin("DELETE", f"/api/v1/sellers/{seller_id}?event_version={event_version}", None)

    async def verify_seller(self, seller_id: str) -> dict[str, Any]:
        return await self._admin("POST", f"/api/v1/sellers/{seller_id}/verify", None)

    async def list_sellers(self) -> list[dict[str, Any]]:
        client = self._require_client()
        try:
            response = await client.get("/api/v1/sellers", headers=self._headers())
        except httpx.RequestError as error:
            raise EgressAdminError(f"Шлюз WB недоступен: {error}") from error
        if response.status_code != 200:
            raise EgressAdminError(f"Шлюз ответил HTTP {response.status_code}", status_code=response.status_code)
        try:
            return response.json()
        except ValueError as error:
            raise EgressAdminError("Шлюз вернул не JSON на запрос списка селлеров") from error

    async def _admin(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        client = self._require_client()
        try:
            response = await client.request(method, path, json=payload, headers=self._headers())
        except httpx.RequestError as error:
            raise EgressAdminError(f"Шлюз WB недоступен: {error}") from error
        if response.status_code == 200:
            try:
                return response.json()
            except ValueError as error:
                raise EgressAdminError("Шлюз вернул не JSON на управляющий вызов") from error
        raise EgressAdminError(
            self._detail(response) or f"Шлюз ответил HTTP {response.status_code}",
            status_code=response.status_code,
        )

    # --- Вспомогательное -------------------------------------------------

    def _headers(self) -> dict[str, str]:
        now = int(time.time())
        token = jwt.encode(
            {
                "aud": self._config.audience,
                "jti": uuid.uuid4().hex,
                "iat": now,
                "exp": now + self._config.jwt_ttl_seconds,
            },
            self._config.jwt_secret,
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    def _require_client(self) -> httpx.AsyncClient:
        # Ленивая инициализация: одному клиенту рады и API-процесс, и воркеры,
        # и никому из них не нужен отдельный этап start().
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url.rstrip("/"),
                verify=self._verify,
                timeout=self._timeout,
                follow_redirects=False,
            )
        return self._client

    @staticmethod
    def _access_error(api_name: str, category: str) -> str:
        if category:
            return f"{api_name}: ключ не имеет доступа к категории «{category}»"
        return f"{api_name}: ключ недействителен или не имеет доступа"

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return ""
        detail = payload.get("detail")
        return detail if isinstance(detail, str) else ""


class _Retry:
    __slots__ = ("reason", "delay")

    def __init__(self, *, reason: str, delay: float) -> None:
        self.reason = reason
        self.delay = delay
