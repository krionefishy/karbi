import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::-([^}]*))?}")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "1", "yes", "on"}:
        return True
    if isinstance(value, str) and value.lower() in {"false", "0", "no", "off", ""}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _expand_environment(value: Any) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name, default = match.groups()
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise ValueError(f"Environment variable {name} is required by the config")

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_environment(item) for key, item in value.items()}
    return value


@dataclass(frozen=True, slots=True)
class AppConfig:
    environment: str = "local"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    cors_origins: tuple[str, ...] = ()
    trust_proxy_headers: bool = False


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    host: str = "localhost"
    port: int = 5432
    user: str = "karbi"
    password: str = field(default="karbi", repr=False)
    database: str = "karbi"
    echo: bool = False
    pool_size: int = 5
    max_overflow: int = 5

    @property
    def url(self) -> str:
        return (
            f"postgresql+asyncpg://{quote_plus(self.user)}:{quote_plus(self.password)}@"
            f"{self.host}:{self.port}/{quote_plus(self.database)}"
        )


@dataclass(frozen=True, slots=True)
class RedisConfig:
    host: str = "localhost"
    port: int = 6379
    database: int = 0
    password: str = field(default="", repr=False)

    @property
    def url(self) -> str:
        credentials = f":{quote_plus(self.password)}@" if self.password else ""
        return f"redis://{credentials}{self.host}:{self.port}/{self.database}"


@dataclass(frozen=True, slots=True)
class KafkaConfig:
    enabled: bool = False
    bootstrap_servers: str = "localhost:9092"
    consumer_group: str = "karbi"
    max_request_size: int = 25_165_824
    topic_partitions: int = 3
    topic_replication_factor: int = 1


@dataclass(frozen=True, slots=True)
class S3Config:
    enabled: bool = False
    endpoint_url: str = ""
    tenant_id: str = ""
    key_id: str = ""
    secret_key: str = field(default="", repr=False)
    bucket: str = ""
    region: str = "ru-central-1"
    verify_tls: bool = True


@dataclass(frozen=True, slots=True)
class AuthConfig:
    jwt_secret: str = field(default="change-me", repr=False)
    algorithm: str = "HS256"
    access_token_ttl_seconds: int = 86_400
    refresh_token_ttl_seconds: int = 604_800
    issuer: str = "karbi"
    audience: str = "karbi-api"


@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    enabled: bool = True
    # Весь офис приходит с одного адреса, а интерфейс опрашивает сервер каждые
    # несколько секунд, пока идёт синхронизация: 30 запросов в минуту такой
    # интерфейс выбирает вдвоём в двух вкладках. Это заслон от злоупотребления,
    # а не от обычной работы.
    requests: int = 120
    window_seconds: int = 60


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    poll_interval_seconds: int = 30
    review_sync_hour: int = 0
    review_sync_minute: int = 30
    review_sync_timezone: str = "Europe/Moscow"
    feedback_page_size: int = 5000
    job_lease_seconds: int = 1800
    job_max_attempts: int = 3
    job_retry_backoff_seconds: int = 300
    run_max_age_seconds: int = 21_600


@dataclass(frozen=True, slots=True)
class TurnoverConfig:
    """Когда собираем остатки и заказы и когда шлём дайджест. Часы московские."""

    timezone: str = "Europe/Moscow"
    # Четыре среза в сутки: одна точка в день не отличает «лежал полный склад»
    # от «утром распродали и вечером пополнили».
    stock_slot_hours: tuple[int, ...] = (3, 9, 15, 21)
    orders_hour: int = 3
    orders_minute: int = 20
    calculation_hour: int = 3
    calculation_minute: int = 40
    digest_hour: int = 10
    digest_minute: int = 0
    # Трёхдневное окно: свежий темп продаж вместо усреднённого за две недели.
    orders_window_days: int = 3
    orders_backfill_days: int = 3
    # Перекрытие при инкрементальной догрузке: отменённый заказ приезжает заново
    # с новым lastChangeDate, но запас лишних суток дешевле пропущенной отмены.
    orders_overlap_hours: int = 24
    threshold_days: int = 10
    # Карточка беднее трёх фото в выдаче WB почти не показывается, поэтому её
    # оборачиваемость никого не спасает: в расчёт и в дайджест такие не идут.
    min_photos: int = 3
    snapshot_retention_days: int = 60
    order_retention_days: int = 45
    # Глубина региональной базы для подсорта: доля округа в спросе считается по
    # этому окну, и на столько же назад догружается история при подключении.
    # Полгода — то, за что менеджер и раньше собирал отчёт руками.
    region_history_days: int = 180
    # Сколько суток истории догружается за один цикл воркера. Каждые сутки —
    # это запрос в «Статистику», и её бюджет делится с обычной догрузкой
    # заказов: догрузка полугода растянута намеренно, она разовая.
    region_backfill_step_days: int = 2
    notification_bot: str = "turnover-alerts"


@dataclass(frozen=True, slots=True)
class FbsDistributionConfig:
    """Когда автоматизация распределения сверяет склады. Часы московские."""

    timezone: str = "Europe/Moscow"
    # Справочник объектов и склады кабинета меняются редко: раз в сутки хватает,
    # а внеплановую сверку оператор запускает кнопкой.
    mirror_hour: int = 4
    mirror_minute: int = 30
    # Сколько снимок 1С считается пригодным для расчёта. Обмен планируется раз
    # в 15 минут, час даёт запас на несколько пропущенных циклов.
    snapshot_max_age_minutes: int = 60
    # Через сколько повторять сверку кабинета после неудачной попытки.
    mirror_retry_minutes: int = 15
    # Токен, которым 1С подписывает входящие снимки остатков. Пустой токен
    # значит «обмен не настроен»: приёмный эндпоинт отвечает 503, а не пускает
    # всех подряд.
    onec_token: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class CardChecklistConfig:
    """Чек-лист карточки: когда перечитывать карточки и два порога, согласованных с селлером. Часы московские."""

    timezone: str = "Europe/Moscow"
    # После утреннего снимка остатков (03:00) и ночного прогона отзывов: к
    # рабочему дню таблица собрана из свежих чисел.
    collect_hour: int = 5
    collect_minute: int = 0
    # Через сколько повторять сбор после неудачи. Ответ 4XX стоит WB десяти
    # запросов, поэтому чаще раза в час долбить сломанный ключ незачем.
    retry_minutes: int = 60
    # Какие товары попадают в таблицу: только с остатком от стольких штук.
    min_stock: int = 10
    # По инструкции чек-листа «фото-контент установлен» — хватает одного.
    min_photos: int = 1
    # Справочник характеристик предмета меняется редко; неделя — компромисс
    # между свежестью и запросами на каждый предмет каждого селлера.
    subject_ttl_hours: int = 168


@dataclass(frozen=True, slots=True)
class RelayConfig:
    """The messenger relay outside Russia: the only host allowed to reach the messenger.

    No bot tokens here — they live on the relay. This side knows an address, a
    shared JWT secret and the certificate it pins.
    """

    base_url: str = ""
    jwt_secret: str = field(default="", repr=False)
    issuer: str = "marketplace-auto"
    # Two audiences on one secret: what we mint going out, what we accept coming
    # back. Equal values would let a token be replayed in the other direction.
    audience: str = "relay"
    inbound_audience: str = "main"
    jwt_ttl_seconds: int = 300
    # Релей умеет присылать картинку рядом с текстом; выключено, пока релей не обновлён.
    photos_enabled: bool = False
    jwt_leeway_seconds: int = 30
    # Сколько релей держит наш запрос за апдейтами, если сказать нечего.
    updates_wait_seconds: int = 25
    # "true"/"false", or a path to the relay certificate to pin. Never disable
    # verification in production: the JWT rides on this connection.
    verify: str = "true"
    request_timeout_seconds: int = 60


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    """Delivery pacing and invite lifetimes; the messenger itself is the relay's business."""

    api_base_url: str = "https://api.telegram.org"
    poll_timeout_seconds: int = 25
    request_timeout_seconds: int = 40
    bot_refresh_seconds: int = 60
    delivery_interval_seconds: float = 1.0
    send_max_attempts: int = 5
    send_retry_backoff_seconds: int = 30
    invite_link_ttl_hours: int = 72


@dataclass(frozen=True, slots=True)
class EgressConfig:
    """The wb-egress gateway: seller keys, the WB throttle and per-seller IPs live there.

    This service no longer reaches *.wildberries.ru on its own — every call
    carries a seller_id and goes through the gateway, which signs it and sends
    it from the seller's pinned address.
    """

    base_url: str = ""
    jwt_secret: str = field(default="", repr=False)
    audience: str = "wb-egress:karbi"
    jwt_ttl_seconds: int = 300
    # "true"/"false", or a path to the gateway certificate to pin. Never disable
    # verification in production: the JWT rides on this connection.
    verify: str = "true"
    # The gateway may hold a background call in its queue for up to ~120s and
    # then spend up to 60s talking to WB — the client outlives both.
    request_timeout_seconds: int = 200


@dataclass(frozen=True, slots=True)
class FbsStocksConfig:
    """Таблица остатков FBS: как часто опрашивать склады кабинетов."""

    timezone: str = "Europe/Moscow"
    # Селлер правит кабинет днём и хочет видеть результат в тот же день, а
    # каждый опрос — запрос на склад: раз в час на ~35 складов кабинета терпимо.
    poll_minutes: int = 60
    # Пауза перед повтором после неудачи: не чаще четверти часа долбить ключ,
    # который отвечает ошибкой.
    retry_minutes: int = 15


@dataclass(frozen=True, slots=True)
class CoreMirrorConfig:
    """Зеркало WB в wb_core: когда воркер обходит всех активных селлеров. Часы московские."""

    timezone: str = "Europe/Moscow"
    # Те же четыре среза, что у оборачиваемости: когда она перейдёт на зеркало,
    # её ряд снимков не сдвинется.
    stock_slot_hours: tuple[int, ...] = (3, 9, 15, 21)
    # Каталог по расписанию, а не только по событию: новая карточка селлера
    # иначе не появится, пока кто-то не нажмёт «обновить».
    catalog_hour: int = 2
    catalog_minute: int = 30
    # Отзывы — три полных прохода по фидбэкам селлера, раз в сутки достаточно.
    reviews_hour: int = 4
    reviews_minute: int = 0
    # Пауза перед повтором после неудачи: ключ, отвечающий ошибкой, не долбим.
    retry_minutes: int = 30
    # Сколько дней остаток считается пригодным для потребителей зеркала.
    stock_fresh_days: int = 2
    # Сборочные задания FBS: свежие перечитываются каждый час, пока не лягут в
    # поставку; живой список WB помнит три месяца, дальше архив помесячно.
    orders_interval_minutes: int = 60
    orders_history_months: int = 6
    orders_retention_days: int = 400
    # Поставки, склады продавца и объекты WB — раз в сутки, до сбора штрафов.
    supplies_hour: int = 5
    supplies_minute: int = 0
    # Чаты с покупателями: лента дочитывается от курсора, один запрос на селлера,
    # если нового нет. Первый сбор берёт историю на `chats_history_days` порциями
    # по `chats_pages_per_run` страниц (в странице 50 событий).
    chats_interval_minutes: int = 30
    chats_history_days: int = 90
    chats_pages_per_run: int = 100
    chats_retention_days: int = 180
    # Остатки по складам WB для подсорта: асинхронный отчёт, создание и
    # скачивание — раз в минуту на ключ. Раз в шесть часов хватает, чтобы
    # логист утром видел остаток после ночных отгрузок.
    remains_interval_minutes: int = 360


@dataclass(frozen=True, slots=True)
class ReviewChatsConfig:
    """Чаты после отзыва: как считать ответ покупателя. Своего сбора нет — читает зеркало чатов."""

    timezone: str = "Europe/Moscow"
    # Сколько ждём ответа. Чат у покупателя с продавцом один: без окна вопрос про
    # другой заказ через две недели засчитался бы как ответ на наше сообщение.
    reply_window_hours: int = 48
    # Отчёт считается при чтении, потолок периода держит запрос в разумных пределах.
    max_period_days: int = 92
    # Сколько дней до запуска рассылки берётся за «как было»: одно и то же окно
    # при любом выбранном периоде, чтобы сравнение не плыло вместе с датами.
    baseline_days: int = 14
    # Не позже скольки минут после автосообщения WB сообщение из API — наше. Рассылка
    # укладывается в полторы минуты; позже отвечают менеджеры через сторонний клиент.
    # Если программа рассылки начнёт задерживаться, поднять порог здесь.
    follow_up_within_minutes: int = 15


@dataclass(frozen=True, slots=True)
class FbsPenaltiesConfig:
    """Штрафы FBS: когда перечитывать фин. отчёт и на какую глубину. Часы московские."""

    timezone: str = "Europe/Moscow"
    # Суточный отчёт за день появляется на следующий день, час неизвестен: список
    # спрашивается каждые `poll_hours` — это один запрос, а читаются только новые.
    poll_hours: int = 2
    # Лимит детализации — запрос в минуту, его держит шлюз; пауза нужна только
    # после настоящей ошибки и чтобы дочитать оставшиеся отчёты следующим проходом.
    retry_minutes: int = 10
    # Список отчётов смотрится на две недели назад — новые появляются по понедельникам;
    # первый сбор кабинета — три месяца, за штрафами она приходит спустя недели.
    report_window_days: int = 14
    report_backfill_days: int = 90
    # Сколько отчётов дочитывать за проход: по минуте на страницу, heartbeat раз в 15.
    reports_per_run: int = 6


@dataclass(frozen=True, slots=True)
class ReturnsConfig:
    """Возвраты WB: как часто перечитывать отчёт и заявки, когда слать дайджест. Часы московские."""

    timezone: str = "Europe/Moscow"
    # Отчёт о возвратах — запрос в минуту на кабинет (держит шлюз), заявки — 20 в минуту.
    # Раз в десять минут хватает: возврат едет в ПВЗ днями, а не минутами.
    poll_minutes: int = 10
    retry_minutes: int = 5
    # Отчёт отдаётся целиком за окно, без страниц; больше 31 дня WB не даёт.
    report_window_days: int = 14
    report_backfill_days: int = 31
    # Архив заявок большой и меняется только решениями продавца.
    claims_archive_hours: int = 6
    # Утренний дайджест того, что едет в ПВЗ.
    digest_hour: int = 9
    digest_minute: int = 0
    notification_bot: str = "wb-returns"
    # Сколько закрытых возвратов и заявок показывать в истории на странице.
    history_limit: int = 300
    # Откуда менеджер скачивает расширение и куда ведут ссылки на картинку QR.
    public_base_url: str = "http://localhost:5173"
    extension_download_path: str = "/extension/marketplace-auto-returns.zip"
    pairing_ttl_minutes: int = 15
    # Во сколько считать, что код на сегодня так и не пришёл; молчание установки дольше — тревога.
    code_alert_hour: int = 1
    install_silent_hours: int = 24


@dataclass(frozen=True, slots=True)
class PodsortConfig:
    """Подсорт WB: сколько дней заказов держать и как быстро их догружать. Часы московские."""

    timezone: str = "Europe/Moscow"
    # Три календарных месяца книги плюс запас; дальше WB заказы и не отдаёт.
    history_days: int = 92
    # Сутки — запрос раз в минуту на ключ (лимит «Статистики», общий с
    # оборачиваемостью): столько дней за проход, потом следующий кабинет.
    days_per_run: int = 5
    # WB дописывает заказы с опозданием: сутки перечитываются, пока их не
    # прочитали через столько часов после полуночи, и не чаще раза в refresh.
    settle_hours: int = 6
    refresh_minutes: int = 60
    retry_minutes: int = 15


@dataclass(frozen=True, slots=True)
class Settings:
    app: AppConfig
    database: DatabaseConfig
    redis: RedisConfig
    kafka: KafkaConfig
    s3: S3Config
    auth: AuthConfig
    rate_limit: RateLimitConfig
    worker: WorkerConfig
    egress: EgressConfig
    telegram: TelegramConfig
    relay: RelayConfig
    turnover: TurnoverConfig
    fbs_distribution: FbsDistributionConfig
    card_checklist: CardChecklistConfig
    fbs_stocks: FbsStocksConfig
    core_mirror: CoreMirrorConfig
    fbs_penalties: FbsPenaltiesConfig
    review_chats: ReviewChatsConfig
    returns: ReturnsConfig
    podsort: PodsortConfig

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        app = dict(data.get("app", {}))
        app["trust_proxy_headers"] = _as_bool(app.get("trust_proxy_headers", False))
        database = dict(data.get("database", {}))
        database["port"] = int(database.get("port", 5432))
        database["echo"] = _as_bool(database.get("echo", False))
        database["pool_size"] = int(database.get("pool_size", 5))
        database["max_overflow"] = int(database.get("max_overflow", 5))
        redis = dict(data.get("redis", {}))
        redis["port"] = int(redis.get("port", 6379))
        redis["database"] = int(redis.get("database", 0))
        kafka = dict(data.get("kafka", {}))
        kafka["enabled"] = _as_bool(kafka.get("enabled", False))
        kafka["max_request_size"] = int(kafka.get("max_request_size", 25_165_824))
        kafka["topic_partitions"] = int(kafka.get("topic_partitions", 3))
        kafka["topic_replication_factor"] = int(kafka.get("topic_replication_factor", 1))
        s3 = dict(data.get("s3", {}))
        s3["enabled"] = _as_bool(s3.get("enabled", False))
        s3["verify_tls"] = _as_bool(s3.get("verify_tls", True))
        auth = dict(data.get("auth", {}))
        auth["access_token_ttl_seconds"] = int(auth.get("access_token_ttl_seconds", 86_400))
        auth["refresh_token_ttl_seconds"] = int(auth.get("refresh_token_ttl_seconds", 604_800))
        rate_limit = dict(data.get("rate_limit", {}))
        rate_limit["enabled"] = _as_bool(rate_limit.get("enabled", True))
        rate_limit["requests"] = int(rate_limit.get("requests", 120))
        rate_limit["window_seconds"] = int(rate_limit.get("window_seconds", 60))
        worker = dict(data.get("worker", {}))
        worker["poll_interval_seconds"] = int(worker.get("poll_interval_seconds", 30))
        worker["review_sync_hour"] = int(worker.get("review_sync_hour", 0))
        worker["review_sync_minute"] = int(worker.get("review_sync_minute", 30))
        worker["feedback_page_size"] = int(worker.get("feedback_page_size", 5000))
        worker["job_lease_seconds"] = int(worker.get("job_lease_seconds", 1800))
        worker["job_max_attempts"] = int(worker.get("job_max_attempts", 3))
        worker["job_retry_backoff_seconds"] = int(worker.get("job_retry_backoff_seconds", 300))
        worker["run_max_age_seconds"] = int(worker.get("run_max_age_seconds", 21_600))
        egress = dict(data.get("egress", {}))
        for key in ("jwt_ttl_seconds", "request_timeout_seconds"):
            if key in egress:
                egress[key] = int(egress[key])
        turnover = dict(data.get("turnover", {}))
        if "stock_slot_hours" in turnover:
            turnover["stock_slot_hours"] = tuple(int(hour) for hour in turnover["stock_slot_hours"])
        for key in (
            "orders_hour",
            "orders_minute",
            "calculation_hour",
            "calculation_minute",
            "digest_hour",
            "digest_minute",
            "orders_window_days",
            "orders_backfill_days",
            "orders_overlap_hours",
            "threshold_days",
            "min_photos",
            "snapshot_retention_days",
            "order_retention_days",
            "region_history_days",
            "region_backfill_step_days",
        ):
            if key in turnover:
                turnover[key] = int(turnover[key])
        fbs_distribution = dict(data.get("fbs_distribution", {}))
        for key in ("mirror_hour", "mirror_minute", "snapshot_max_age_minutes", "mirror_retry_minutes"):
            if key in fbs_distribution:
                fbs_distribution[key] = int(fbs_distribution[key])
        card_checklist = dict(data.get("card_checklist", {}))
        for key in (
            "collect_hour",
            "collect_minute",
            "retry_minutes",
            "min_stock",
            "min_photos",
            "subject_ttl_hours",
        ):
            if key in card_checklist:
                card_checklist[key] = int(card_checklist[key])
        fbs_stocks = dict(data.get("fbs_stocks", {}))
        for key in ("poll_minutes", "retry_minutes"):
            if key in fbs_stocks:
                fbs_stocks[key] = int(fbs_stocks[key])
        core_mirror = dict(data.get("core_mirror", {}))
        for key in (
            "catalog_hour",
            "catalog_minute",
            "reviews_hour",
            "reviews_minute",
            "retry_minutes",
            "stock_fresh_days",
            "orders_interval_minutes",
            "orders_history_months",
            "orders_retention_days",
            "chats_interval_minutes",
            "chats_history_days",
            "chats_pages_per_run",
            "chats_retention_days",
            "supplies_hour",
            "supplies_minute",
            "remains_interval_minutes",
        ):
            if key in core_mirror:
                core_mirror[key] = int(core_mirror[key])
        if "stock_slot_hours" in core_mirror:
            core_mirror["stock_slot_hours"] = tuple(int(hour) for hour in core_mirror["stock_slot_hours"])
        review_chats = dict(data.get("review_chats", {}))
        for key in ("reply_window_hours", "max_period_days", "baseline_days", "follow_up_within_minutes"):
            if key in review_chats:
                review_chats[key] = int(review_chats[key])
        fbs_penalties = dict(data.get("fbs_penalties", {}))
        for key in (
            "poll_hours",
            "retry_minutes",
            "report_window_days",
            "report_backfill_days",
            "reports_per_run",
        ):
            if key in fbs_penalties:
                fbs_penalties[key] = int(fbs_penalties[key])
        podsort = dict(data.get("podsort", {}))
        for key in ("history_days", "days_per_run", "settle_hours", "refresh_minutes", "retry_minutes"):
            if key in podsort:
                podsort[key] = int(podsort[key])
        returns = dict(data.get("returns", {}))
        for key in (
            "poll_minutes",
            "retry_minutes",
            "report_window_days",
            "report_backfill_days",
            "claims_archive_hours",
            "digest_hour",
            "digest_minute",
            "history_limit",
            "pairing_ttl_minutes",
            "code_alert_hour",
            "install_silent_hours",
        ):
            if key in returns:
                returns[key] = int(returns[key])
        telegram = dict(data.get("telegram", {}))
        for key in (
            "poll_timeout_seconds",
            "request_timeout_seconds",
            "bot_refresh_seconds",
            "send_max_attempts",
            "send_retry_backoff_seconds",
            "invite_link_ttl_hours",
        ):
            if key in telegram:
                telegram[key] = int(telegram[key])
        if "delivery_interval_seconds" in telegram:
            telegram["delivery_interval_seconds"] = float(telegram["delivery_interval_seconds"])
        relay = dict(data.get("relay", {}))
        relay["photos_enabled"] = _as_bool(relay.get("photos_enabled", False))
        for key in ("jwt_ttl_seconds", "jwt_leeway_seconds", "updates_wait_seconds", "request_timeout_seconds"):
            if key in relay:
                relay[key] = int(relay[key])
        settings = cls(
            app=AppConfig(**{**app, "cors_origins": tuple(app.get("cors_origins", []))}),
            database=DatabaseConfig(**database),
            redis=RedisConfig(**redis),
            kafka=KafkaConfig(**kafka),
            s3=S3Config(**s3),
            auth=AuthConfig(**auth),
            rate_limit=RateLimitConfig(**rate_limit),
            worker=WorkerConfig(**worker),
            egress=EgressConfig(**egress),
            telegram=TelegramConfig(**telegram),
            relay=RelayConfig(**relay),
            turnover=TurnoverConfig(**turnover),
            fbs_distribution=FbsDistributionConfig(**fbs_distribution),
            card_checklist=CardChecklistConfig(**card_checklist),
            fbs_stocks=FbsStocksConfig(**fbs_stocks),
            core_mirror=CoreMirrorConfig(**core_mirror),
            fbs_penalties=FbsPenaltiesConfig(**fbs_penalties),
            review_chats=ReviewChatsConfig(**review_chats),
            returns=ReturnsConfig(**returns),
            podsort=PodsortConfig(**podsort),
        )
        settings.validate_values()
        return settings

    def validate_values(self) -> None:
        if not 0 <= self.worker.review_sync_hour <= 23:
            raise ValueError("worker.review_sync_hour must be between 0 and 23")
        if not 0 <= self.worker.review_sync_minute <= 59:
            raise ValueError("worker.review_sync_minute must be between 0 and 59")
        if not 1 <= self.worker.feedback_page_size <= 5000:
            raise ValueError("worker.feedback_page_size must be between 1 and 5000")
        if self.worker.poll_interval_seconds < 1:
            raise ValueError("worker.poll_interval_seconds must be positive")
        if self.worker.job_lease_seconds < 60:
            raise ValueError("worker.job_lease_seconds must be at least 60")
        if self.worker.job_max_attempts < 1:
            raise ValueError("worker.job_max_attempts must be positive")
        if self.worker.job_retry_backoff_seconds < 1:
            raise ValueError("worker.job_retry_backoff_seconds must be positive")
        if self.worker.run_max_age_seconds <= self.worker.job_lease_seconds:
            raise ValueError("worker.run_max_age_seconds must exceed worker.job_lease_seconds")
        checklist = self.card_checklist
        if not 0 <= checklist.collect_hour <= 23:
            raise ValueError("card_checklist.collect_hour must be between 0 and 23")
        if not 0 <= checklist.collect_minute <= 59:
            raise ValueError("card_checklist.collect_minute must be between 0 and 59")
        if checklist.retry_minutes < 1:
            raise ValueError("card_checklist.retry_minutes must be positive")
        if min(checklist.min_stock, checklist.min_photos) < 0:
            raise ValueError("card_checklist thresholds must not be negative")
        if checklist.subject_ttl_hours < 1:
            raise ValueError("card_checklist.subject_ttl_hours must be positive")
        if self.fbs_stocks.poll_minutes < 1:
            raise ValueError("fbs_stocks.poll_minutes must be positive")
        if self.fbs_stocks.retry_minutes < 1:
            raise ValueError("fbs_stocks.retry_minutes must be positive")
        mirror = self.core_mirror
        if not mirror.stock_slot_hours or any(not 0 <= hour <= 23 for hour in mirror.stock_slot_hours):
            raise ValueError("core_mirror.stock_slot_hours must contain hours between 0 and 23")
        if len(set(mirror.stock_slot_hours)) != len(mirror.stock_slot_hours):
            raise ValueError("core_mirror.stock_slot_hours must not repeat an hour")
        for name, hour, minute in (
            ("catalog", mirror.catalog_hour, mirror.catalog_minute),
            ("reviews", mirror.reviews_hour, mirror.reviews_minute),
            ("supplies", mirror.supplies_hour, mirror.supplies_minute),
        ):
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError(f"core_mirror.{name}_hour/minute must be a valid time of day")
        if mirror.retry_minutes < 1 or mirror.stock_fresh_days < 1:
            raise ValueError("core_mirror.retry_minutes and stock_fresh_days must be positive")
        if min(mirror.orders_interval_minutes, mirror.orders_history_months, mirror.orders_retention_days) < 1:
            raise ValueError("core_mirror.orders_* must be positive")
        if (
            min(
                mirror.chats_interval_minutes,
                mirror.chats_history_days,
                mirror.chats_pages_per_run,
                mirror.chats_retention_days,
            )
            < 1
        ):
            raise ValueError("core_mirror.chats_* must be positive")
        if mirror.remains_interval_minutes < 1:
            raise ValueError("core_mirror.remains_interval_minutes must be positive")
        chats = self.review_chats
        if (
            min(chats.reply_window_hours, chats.max_period_days, chats.baseline_days, chats.follow_up_within_minutes)
            < 1
        ):
            raise ValueError("review_chats.* windows must be positive")
        penalties = self.fbs_penalties
        if (
            min(
                penalties.poll_hours,
                penalties.retry_minutes,
                penalties.report_window_days,
                penalties.report_backfill_days,
                penalties.reports_per_run,
            )
            < 1
        ):
            raise ValueError("fbs_penalties.retry_minutes, report windows and reports_per_run must be positive")
        returns = self.returns
        if (
            min(
                returns.poll_minutes,
                returns.retry_minutes,
                returns.report_window_days,
                returns.report_backfill_days,
                returns.claims_archive_hours,
                returns.history_limit,
            )
            < 1
        ):
            raise ValueError(
                "returns: poll and retry minutes, windows, claims_archive_hours and history_limit must be positive"
            )
        if returns.report_backfill_days > 31 or returns.report_window_days > 31:
            raise ValueError("returns.report_window_days and report_backfill_days must not exceed 31 (WB limit)")
        if not 0 <= returns.digest_hour <= 23 or not 0 <= returns.digest_minute <= 59:
            raise ValueError("returns.digest_hour must be 0..23 and digest_minute 0..59")
        if not returns.notification_bot:
            raise ValueError("returns.notification_bot must be set")
        podsort = self.podsort
        if (
            min(podsort.history_days, podsort.days_per_run, podsort.refresh_minutes, podsort.retry_minutes) < 1
            or podsort.settle_hours < 0
        ):
            raise ValueError("podsort: history_days, days_per_run, refresh and retry minutes must be positive")
        if not self.turnover.stock_slot_hours:
            raise ValueError("turnover.stock_slot_hours must contain at least one hour")
        if any(not 0 <= hour <= 23 for hour in self.turnover.stock_slot_hours):
            raise ValueError("turnover.stock_slot_hours must contain hours between 0 and 23")
        if len(set(self.turnover.stock_slot_hours)) != len(self.turnover.stock_slot_hours):
            raise ValueError("turnover.stock_slot_hours must not repeat an hour")
        if self.turnover.orders_window_days < 1:
            raise ValueError("turnover.orders_window_days must be positive")
        if self.turnover.threshold_days < 1:
            raise ValueError("turnover.threshold_days must be positive")
        if self.turnover.min_photos < 0:
            raise ValueError("turnover.min_photos must not be negative")
        if self.turnover.region_history_days < 1:
            raise ValueError("turnover.region_history_days must be positive")
        if self.turnover.region_backfill_step_days < 1:
            raise ValueError("turnover.region_backfill_step_days must be positive")
        if self.turnover.order_retention_days <= self.turnover.orders_window_days:
            # Pruning inside the window would erase the very orders the metric divides by.
            raise ValueError("turnover.order_retention_days must exceed turnover.orders_window_days")
        if self.turnover.snapshot_retention_days <= self.turnover.orders_window_days:
            raise ValueError("turnover.snapshot_retention_days must exceed turnover.orders_window_days")
        if not 0 <= self.fbs_distribution.mirror_hour <= 23:
            raise ValueError("fbs_distribution.mirror_hour must be between 0 and 23")
        if not 0 <= self.fbs_distribution.mirror_minute <= 59:
            raise ValueError("fbs_distribution.mirror_minute must be between 0 and 59")
        if self.fbs_distribution.snapshot_max_age_minutes < 1:
            raise ValueError("fbs_distribution.snapshot_max_age_minutes must be positive")
        if self.fbs_distribution.mirror_retry_minutes < 1:
            raise ValueError("fbs_distribution.mirror_retry_minutes must be positive")
        try:
            ZoneInfo(self.fbs_distribution.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("fbs_distribution.timezone must be a known timezone") from error
        if self.turnover.timezone != "Europe/Moscow":
            # WB statistics serve every date in Moscow time with no zone attached;
            # scheduling this automation on any other clock would silently shift
            # its day boundaries against the data.
            raise ValueError("turnover.timezone must be Europe/Moscow: WB statistics dates are always Moscow time")
        if self.telegram.poll_timeout_seconds < 1:
            raise ValueError("telegram.poll_timeout_seconds must be positive")
        if self.telegram.request_timeout_seconds <= self.telegram.poll_timeout_seconds:
            # Long polling holds the connection for the whole poll timeout, so a
            # request timeout below it would cancel every idle poll.
            raise ValueError("telegram.request_timeout_seconds must exceed telegram.poll_timeout_seconds")
        if self.telegram.send_max_attempts < 1:
            raise ValueError("telegram.send_max_attempts must be positive")
        if self.telegram.invite_link_ttl_hours < 1:
            raise ValueError("telegram.invite_link_ttl_hours must be positive")
        if self.telegram.delivery_interval_seconds <= 0:
            raise ValueError("telegram.delivery_interval_seconds must be positive")
        try:
            ZoneInfo(self.worker.review_sync_timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("worker.review_sync_timezone is invalid") from error

    def validate_runtime_secrets(self) -> None:
        if self.app.environment not in {"production", "prod"}:
            return
        invalid: list[str] = []
        if len(self.auth.jwt_secret) < 32 or self.auth.jwt_secret == "change-me":
            invalid.append("auth.jwt_secret")
        if not self.database.password:
            invalid.append("database.password")
        if not self.redis.password:
            invalid.append("redis.password")
        if "*" in self.app.cors_origins:
            invalid.append("app.cors_origins cannot contain '*' in production")
        if not self.relay.base_url:
            invalid.append("relay.base_url")
        if len(self.relay.jwt_secret) < 32:
            invalid.append("relay.jwt_secret")
        if self.relay.verify.lower() in {"false", "0", "no"}:
            invalid.append("relay.verify cannot disable TLS verification in production")
        if self.relay.audience == self.relay.inbound_audience:
            invalid.append("relay.audience and relay.inbound_audience must differ")
        if not self.egress.base_url:
            invalid.append("egress.base_url")
        if len(self.egress.jwt_secret) < 32:
            invalid.append("egress.jwt_secret")
        if self.egress.verify.lower() in {"false", "0", "no"}:
            invalid.append("egress.verify cannot disable TLS verification in production")
        if self.s3.enabled and not all((self.s3.key_id, self.s3.secret_key, self.s3.bucket)):
            invalid.append("s3 credentials and bucket")
        if invalid:
            raise ValueError(f"Unsafe or missing production settings: {', '.join(invalid)}")


def _default_config_path() -> Path:
    return Path(__file__).with_name("config.local.yaml")


@lru_cache
def load_settings(config_path: str | Path | None = None) -> Settings:
    selected_path: str | Path = config_path or os.getenv("CONFIG_PATH") or _default_config_path()
    path = Path(selected_path)
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Settings.from_dict(_expand_environment(raw))
