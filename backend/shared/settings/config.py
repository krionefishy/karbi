import os
import re
from dataclasses import dataclass, field
from dataclasses import replace as replace_fields
from datetime import date
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
    stock_slot_hours: tuple[int, ...] = (3, 9, 15, 21)
    orders_hour: int = 3
    orders_minute: int = 20
    calculation_hour: int = 3
    calculation_minute: int = 40
    digest_hour: int = 10
    digest_minute: int = 0
    orders_window_days: int = 3
    orders_backfill_days: int = 3
    orders_overlap_hours: int = 24
    threshold_days: int = 10
    min_photos: int = 3
    snapshot_retention_days: int = 60
    order_retention_days: int = 45
    region_history_days: int = 180
    region_backfill_step_days: int = 2
    notification_bot: str = "turnover-alerts"


@dataclass(frozen=True, slots=True)
class FbsDistributionConfig:
    """Когда автоматизация распределения сверяет склады. Часы московские."""

    timezone: str = "Europe/Moscow"
    mirror_hour: int = 4
    mirror_minute: int = 30
    snapshot_max_age_minutes: int = 60
    mirror_retry_minutes: int = 15
    onec_token: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class CardChecklistConfig:
    """Чек-лист карточки: когда перечитывать карточки и два порога, согласованных с селлером. Часы московские."""

    timezone: str = "Europe/Moscow"
    collect_hour: int = 5
    collect_minute: int = 0
    retry_minutes: int = 60
    min_stock: int = 10
    min_photos: int = 1
    subject_ttl_hours: int = 168


@dataclass(frozen=True, slots=True)
class CardDocumentsConfig:
    """Документы карточек: как часто перечитывать вердикты WB и когда о них говорить. Часы московские.

    Раз в `interval_minutes` читаются только изменённые карточки: курсор по `updatedAt` с
    запасом `cursor_margin_seconds`, WB двигает `updatedAt` при каждом новом результате
    проверки. Раз в `full_interval_hours` — полный обход: только он замечает карточки, ушедшие
    из каталога. В `skip_hours` плановый сбор не начинается: это окна каталога (02:30) и
    чек-листа (05:00). Сбои наблюдения показываются баннерами на странице, `stale_hours`
    — порог баннера «данные устарели».
    """

    timezone: str = "Europe/Moscow"
    interval_minutes: int = 60
    full_interval_hours: int = 24
    cursor_margin_seconds: int = 60
    retry_minutes: int = 30
    stale_hours: int = 9
    expiry_thresholds: tuple[int, ...] = (30, 14, 7)
    digest_hour: int = 10
    digest_minute: int = 0
    skip_hours: tuple[int, ...] = (2, 5)
    notification_bot: str = "card-documents"


@dataclass(frozen=True, slots=True)
class RelayConfig:
    """The messenger relay outside Russia: the only host allowed to reach the messenger.

    No bot tokens here — they live on the relay. This side knows an address, a
    shared JWT secret and the certificate it pins.
    """

    base_url: str = ""
    jwt_secret: str = field(default="", repr=False)
    issuer: str = "marketplace-auto"
    audience: str = "relay"
    inbound_audience: str = "main"
    jwt_ttl_seconds: int = 300
    photos_enabled: bool = False
    jwt_leeway_seconds: int = 30
    updates_wait_seconds: int = 25
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
    verify: str = "true"
    request_timeout_seconds: int = 200


@dataclass(frozen=True, slots=True)
class FbsStocksConfig:
    """Таблица остатков FBS: как часто опрашивать склады кабинетов."""

    timezone: str = "Europe/Moscow"
    poll_minutes: int = 60
    retry_minutes: int = 15


@dataclass(frozen=True, slots=True)
class CoreMirrorConfig:
    """Зеркало WB в wb_core: когда воркер обходит всех активных селлеров. Часы московские."""

    timezone: str = "Europe/Moscow"
    stock_slot_hours: tuple[int, ...] = (3, 9, 15, 21)
    catalog_hour: int = 2
    catalog_minute: int = 30
    reviews_hour: int = 4
    reviews_minute: int = 0
    retry_minutes: int = 30
    stock_fresh_days: int = 2
    orders_interval_minutes: int = 60
    orders_history_months: int = 6
    orders_retention_days: int = 400
    supplies_hour: int = 5
    supplies_minute: int = 0
    chats_interval_minutes: int = 30
    chats_history_days: int = 90
    chats_pages_per_run: int = 100
    chats_retention_days: int = 180
    remains_interval_minutes: int = 360
    sales_reports_interval_minutes: int = 30
    sales_reports_per_run: int = 4
    sales_reports_history_from: str = "2026-01-01"
    adverts_interval_minutes: int = 360
    adverts_windows_per_run: int = 3
    adverts_overlap_days: int = 3
    ozon_interval_minutes: int = 120
    ozon_days_per_run: int = 45
    ozon_overlap_days: int = 3


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """Языковая модель по OpenAI-совместимому API (Cloud.ru Foundation Models).

    Модель задаётся здесь, а не в коде: смена модели — правка одной строки и
    перераскатка. Ключ приходит только из окружения.
    """

    base_url: str = "https://foundation-models.api.cloud.ru/v1"
    model: str = "Qwen/Qwen3-30B-A3B"
    api_key: str = field(default="", repr=False)
    timeout_seconds: int = 60
    max_tokens: int = 150
    temperature: float = 0.0


@dataclass(frozen=True, slots=True)
class SupportBotConfig:
    """Бот поддержки в чатах WB: когда и как часто писать покупателю, который замолчал."""

    timezone: str = "Europe/Moscow"
    window_start_hour: int = 9
    window_end_hour: int = 22
    interval_minutes: int = 60
    jitter_min_minutes: int = 1
    jitter_max_minutes: int = 10
    silence_hours: int = 4
    campaign_days: int = 5
    max_campaigns: int = 1
    daily_cap_per_seller: int = 2500
    min_gap_seconds: int = 3
    tick_minutes: int = 10
    batch_per_tick: int = 40
    history_days: int = 12
    mirror_stale_minutes: int = 90
    max_idle_days: int = 5
    canary_percent: int = 10
    classifier_mode: str = "shadow"
    classifier_prompt_version: str = "v2"
    classifier_retry_minutes: int = 15
    classifier_max_attempts: int = 3
    chat_url_template: str = "https://seller.wildberries.ru/chat-with-clients?chatId={chat_id}"


@dataclass(frozen=True, slots=True)
class ReviewChatsConfig:
    """Чаты после отзыва: как считать ответ покупателя. Своего сбора нет — читает зеркало чатов."""

    timezone: str = "Europe/Moscow"
    reply_window_hours: int = 48
    max_period_days: int = 92
    baseline_days: int = 14
    follow_up_within_minutes: int = 15


@dataclass(frozen=True, slots=True)
class FbsPenaltiesConfig:
    """Штрафы FBS: когда перечитывать фин. отчёт и на какую глубину. Часы московские."""

    timezone: str = "Europe/Moscow"
    poll_hours: int = 2
    retry_minutes: int = 10
    report_window_days: int = 14
    report_backfill_days: int = 90
    reports_per_run: int = 6


@dataclass(frozen=True, slots=True)
class ReturnsConfig:
    """Возвраты WB: как часто перечитывать отчёт и заявки, когда слать дайджест. Часы московские."""

    timezone: str = "Europe/Moscow"
    poll_minutes: int = 10
    retry_minutes: int = 5
    report_window_days: int = 14
    report_backfill_days: int = 31
    claims_archive_hours: int = 6
    notify_hours: tuple[int, ...] = (9, 12, 15, 18)
    notification_bot: str = "wb-returns"
    history_limit: int = 300
    public_base_url: str = "http://localhost:5173"
    extension_download_path: str = "/extension/marketplace-auto-returns.zip"
    pairing_ttl_minutes: int = 15
    code_alert_hour: int = 1
    install_silent_hours: int = 24


@dataclass(frozen=True, slots=True)
class VisionConfig:
    """Модель, читающая скриншоты. Отдельно от текстовой `llm`, чтобы её настройки не задели бота поддержки.

    Модель выбрана пробным прогоном (этап 0): Qwen3.6-35B-A3B без рассуждений — быстро и дёшево;
    `fallback_model` точнее и дороже, ей уходит ремонтный запрос. Ключ — только из окружения.
    """

    base_url: str = "https://foundation-models.api.cloud.ru/v1"
    model: str = "Qwen/Qwen3.6-35B-A3B"
    fallback_model: str = "Qwen/Qwen3.5-397B-A17B"
    enable_thinking: bool = False
    api_key: str = field(default="", repr=False)
    timeout_seconds: int = 60
    max_tokens: int = 1200
    temperature: float = 0.0
    max_image_side: int = 2000


@dataclass(frozen=True, slots=True)
class ScreenshotThresholds:
    """Пороги дедупа. Подлежат калибровке на исторической выгрузке до боевого режима."""

    text_dup: float = 0.90
    text_maybe: float = 0.75
    min_text_len: int = 25
    vlm_ocr_text: float = 0.80
    near_dhash_strict: int = 6
    near_tile_strict: float = 0.97
    near_dhash_soft: int = 12
    near_tile_soft: float = 0.90


@dataclass(frozen=True, slots=True)
class ScreenshotControlConfig:
    """Контроль скриншотов ДБС: режим, сроки хранения, лимиты приёма и сопряжения расширения.

    `mode`: off — модуль выключен; shadow — вердикты пишутся, KPI помечен как предварительный;
    enforce — KPI боевой. Скрины лежат в S3: `s3_bucket` пустой — общий бакет `s3.bucket`,
    ключи в любом случае под префиксом `dbs-screenshots/`.
    """

    mode: str = "shadow"
    kpi_from: str = ""
    timezone: str = "Europe/Moscow"
    poll_seconds: int = 2
    hmac_key: str = field(default="", repr=False)
    s3_bucket: str = ""
    staging_root: str = "/data/screenshots-staging"
    staging_max_used_percent: int = 85
    staging_max_bytes: int = 2_147_483_648
    staging_stale_minutes: int = 15
    max_upload_bytes: int = 15_728_640
    min_side_px: int = 500
    max_image_pixels: int = 25_000_000
    retention_original_days: int = 365
    retention_thumb_days: int = 365
    retention_fields_days: int = 1095
    purge_hour: int = 4
    purge_minute: int = 10
    dedupe_window_days: int = 90
    outcome_ttl_hours: int = 24
    late_upload_hours: int = 72
    clock_skew_minutes: int = 15
    review_age_max_days: int = 120
    burst_per_hour: int = 60
    uploads_per_minute: int = 20
    uploads_per_hour: int = 120
    status_polls_per_minute: int = 120
    ip_requests_per_minute: int = 300
    max_extension_installs: int = 3
    extraction_max_attempts: int = 4
    audit_percent: float = 4.0
    thresholds: ScreenshotThresholds = ScreenshotThresholds()
    image_url_ttl_seconds: int = 300
    pairing_ttl_minutes: int = 15
    pair_attempts_per_window: int = 10
    pair_window_seconds: int = 600
    extension_token_idle_days: int = 60
    link_token_ttl_minutes: int = 15
    notification_bot: str = "screens"
    telegram_enabled: bool = False
    pipeline_version: str = "v1"
    public_base_url: str = "http://localhost:5173"
    extension_download_path: str = "/extension/marketplace-auto-screenshots.zip"
    extension_min_version: str = "0.1.0"
    allowed_url_prefixes: tuple[str, ...] = ("https://seller.wildberries.ru/chat-with-clients",)
    # Модуль выключен принудительно (например, `hmac_key_missing`): `mode` уже `off`, а здесь — почему
    # и какой режим просили. Задаётся при загрузке, в yaml не пишется.
    disabled_reason: str = ""
    requested_mode: str = ""

    def bucket(self, s3: S3Config) -> str:
        return self.s3_bucket or s3.bucket


@dataclass(frozen=True, slots=True)
class FinReportsConfig:
    """Финансовые отчёты: как часто складывать дочитанные зеркалом отчёты реализации. Часы московские."""

    timezone: str = "Europe/Moscow"
    build_interval_minutes: int = 10
    reports_per_run: int = 40
    ozon_days_per_run: int = 60


@dataclass(frozen=True, slots=True)
class PodsortConfig:
    """Подсорт WB: сколько дней заказов держать и как быстро их догружать. Часы московские."""

    timezone: str = "Europe/Moscow"
    history_days: int = 90
    days_per_run: int = 5
    request_interval_seconds: int = 61
    remains_stale_hours: int = 24
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
    fin_reports: FinReportsConfig
    support_bot: SupportBotConfig = SupportBotConfig()
    llm: LLMConfig = LLMConfig()
    card_documents: CardDocumentsConfig = CardDocumentsConfig()
    screenshot_control: ScreenshotControlConfig = ScreenshotControlConfig()
    vision: VisionConfig = VisionConfig()

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
        card_documents = dict(data.get("card_documents", {}))
        for key in (
            "interval_minutes",
            "full_interval_hours",
            "cursor_margin_seconds",
            "retry_minutes",
            "stale_hours",
            "digest_hour",
            "digest_minute",
        ):
            if key in card_documents:
                card_documents[key] = int(card_documents[key] or 0)
        for key in ("expiry_thresholds", "skip_hours"):
            if key in card_documents:
                card_documents[key] = tuple(int(value) for value in card_documents[key] or ())
        if "notification_bot" in card_documents:
            card_documents["notification_bot"] = str(card_documents["notification_bot"])
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
            "sales_reports_interval_minutes",
            "sales_reports_per_run",
            "adverts_interval_minutes",
            "adverts_windows_per_run",
            "adverts_overlap_days",
            "ozon_interval_minutes",
            "ozon_days_per_run",
            "ozon_overlap_days",
        ):
            if key in core_mirror:
                core_mirror[key] = int(core_mirror[key])
        if "sales_reports_history_from" in core_mirror:
            core_mirror["sales_reports_history_from"] = str(core_mirror["sales_reports_history_from"])
        if "stock_slot_hours" in core_mirror:
            core_mirror["stock_slot_hours"] = tuple(int(hour) for hour in core_mirror["stock_slot_hours"])
        review_chats = dict(data.get("review_chats", {}))
        for key in ("reply_window_hours", "max_period_days", "baseline_days", "follow_up_within_minutes"):
            if key in review_chats:
                review_chats[key] = int(review_chats[key])
        support_bot = dict(data.get("support_bot", {}))
        for key, value in list(support_bot.items()):
            if key not in ("timezone", "chat_url_template", "classifier_mode", "classifier_prompt_version"):
                support_bot[key] = int(value)
        llm = dict(data.get("llm", {}))
        for key in ("timeout_seconds", "max_tokens"):
            if key in llm:
                llm[key] = int(llm[key])
        if "temperature" in llm:
            llm["temperature"] = float(llm["temperature"])
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
        for key in (
            "history_days",
            "days_per_run",
            "settle_hours",
            "refresh_minutes",
            "retry_minutes",
            "request_interval_seconds",
            "remains_stale_hours",
        ):
            if key in podsort:
                podsort[key] = int(podsort[key])
        fin_reports = dict(data.get("fin_reports", {}))
        for key in ("build_interval_minutes", "reports_per_run", "ozon_days_per_run"):
            if key in fin_reports:
                fin_reports[key] = int(fin_reports[key])
        returns = dict(data.get("returns", {}))
        for key in (
            "poll_minutes",
            "retry_minutes",
            "report_window_days",
            "report_backfill_days",
            "claims_archive_hours",
            "history_limit",
            "pairing_ttl_minutes",
            "code_alert_hour",
            "install_silent_hours",
        ):
            if key in returns:
                returns[key] = int(returns[key])
        if "notify_hours" in returns:
            returns["notify_hours"] = tuple(int(hour) for hour in returns["notify_hours"])
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
        vision = dict(data.get("vision", {}))
        for key in ("timeout_seconds", "max_tokens", "max_image_side"):
            if key in vision:
                vision[key] = int(vision[key])
        if "temperature" in vision:
            vision["temperature"] = float(vision["temperature"])
        vision["enable_thinking"] = _as_bool(vision.get("enable_thinking", False))
        for key in ("model", "fallback_model"):
            # Пустая переменная окружения из compose не должна стирать выбранную модель.
            if key in vision and not vision[key]:
                del vision[key]
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
            fin_reports=FinReportsConfig(**fin_reports),
            support_bot=SupportBotConfig(**support_bot),
            llm=LLMConfig(**llm),
            card_documents=CardDocumentsConfig(**card_documents),
            screenshot_control=_screenshot_control(dict(data.get("screenshot_control", {}))),
            vision=VisionConfig(**vision),
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
        documents = self.card_documents
        ZoneInfo(documents.timezone)
        if (
            min(
                documents.interval_minutes,
                documents.full_interval_hours,
                documents.retry_minutes,
                documents.stale_hours,
            )
            < 1
        ):
            raise ValueError(
                "card_documents.interval_minutes, full_interval_hours, retry_minutes and stale_hours must be positive"
            )
        if documents.stale_hours * 60 <= documents.interval_minutes:
            raise ValueError("card_documents.stale_hours must exceed interval_minutes")
        if documents.full_interval_hours * 60 < documents.interval_minutes:
            raise ValueError("card_documents.full_interval_hours must not be shorter than interval_minutes")
        if not 0 <= documents.cursor_margin_seconds <= 3600:
            raise ValueError("card_documents.cursor_margin_seconds must be between 0 and 3600")
        if not documents.expiry_thresholds or any(days < 1 for days in documents.expiry_thresholds):
            raise ValueError("card_documents.expiry_thresholds must contain positive days")
        if not 0 <= documents.digest_hour <= 23 or not 0 <= documents.digest_minute <= 59:
            raise ValueError("card_documents.digest_hour/minute must be a valid time of day")
        if any(not 0 <= hour <= 23 for hour in documents.skip_hours) or len(documents.skip_hours) >= 24:
            raise ValueError("card_documents.skip_hours must contain hours between 0 and 23 and leave some free")
        if not documents.notification_bot:
            raise ValueError("card_documents.notification_bot must be set")
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
        if min(mirror.sales_reports_interval_minutes, mirror.sales_reports_per_run) < 1:
            raise ValueError("core_mirror.sales_reports_* must be positive")
        if min(mirror.adverts_interval_minutes, mirror.adverts_windows_per_run) < 1 or mirror.adverts_overlap_days < 0:
            raise ValueError("core_mirror.adverts_* must be positive")
        if min(mirror.ozon_interval_minutes, mirror.ozon_days_per_run) < 1 or mirror.ozon_overlap_days < 0:
            raise ValueError("core_mirror.ozon_* must be positive")
        try:
            date.fromisoformat(mirror.sales_reports_history_from)
        except ValueError as error:
            raise ValueError("core_mirror.sales_reports_history_from must be a date (YYYY-MM-DD)") from error
        chats = self.review_chats
        if (
            min(chats.reply_window_hours, chats.max_period_days, chats.baseline_days, chats.follow_up_within_minutes)
            < 1
        ):
            raise ValueError("review_chats.* windows must be positive")
        bot = self.support_bot
        ZoneInfo(bot.timezone)
        if not 0 <= bot.window_start_hour < bot.window_end_hour <= 24:
            raise ValueError("support_bot.window_start_hour must be earlier than window_end_hour")
        if bot.jitter_min_minutes < 0 or bot.jitter_max_minutes < bot.jitter_min_minutes:
            raise ValueError("support_bot.jitter_* must be a non-negative range")
        if (
            min(
                bot.interval_minutes,
                bot.silence_hours,
                bot.campaign_days,
                bot.max_campaigns,
                bot.daily_cap_per_seller,
                bot.tick_minutes,
                bot.batch_per_tick,
                bot.history_days,
                bot.mirror_stale_minutes,
            )
            < 1
            or bot.min_gap_seconds < 0
        ):
            raise ValueError("support_bot.* limits must be positive")
        if not 1 <= bot.canary_percent <= 100:
            raise ValueError("support_bot.canary_percent must be between 1 and 100")
        if bot.max_idle_days < 1:
            raise ValueError("support_bot.max_idle_days must be positive")
        if bot.history_days <= bot.campaign_days:
            raise ValueError("support_bot.history_days must exceed campaign_days")
        if bot.classifier_mode not in ("off", "shadow", "enforce"):
            raise ValueError("support_bot.classifier_mode must be off, shadow or enforce")
        if min(bot.classifier_retry_minutes, bot.classifier_max_attempts) < 1:
            raise ValueError("support_bot.classifier_* limits must be positive")
        if self.llm.timeout_seconds < 1 or self.llm.max_tokens < 1:
            raise ValueError("llm.timeout_seconds and llm.max_tokens must be positive")
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
        if not returns.notify_hours or any(not 0 <= hour <= 23 for hour in returns.notify_hours):
            raise ValueError("returns.notify_hours must contain hours between 0 and 23")
        if len(set(returns.notify_hours)) != len(returns.notify_hours):
            raise ValueError("returns.notify_hours must not repeat an hour")
        if not returns.notification_bot:
            raise ValueError("returns.notification_bot must be set")
        podsort = self.podsort
        if (
            min(
                podsort.history_days,
                podsort.days_per_run,
                podsort.refresh_minutes,
                podsort.retry_minutes,
                podsort.remains_stale_hours,
            )
            < 1
            or min(podsort.settle_hours, podsort.request_interval_seconds) < 0
        ):
            raise ValueError("podsort: history_days, days_per_run, refresh and retry minutes must be positive")
        if (
            min(
                self.fin_reports.build_interval_minutes,
                self.fin_reports.reports_per_run,
                self.fin_reports.ozon_days_per_run,
            )
            < 1
        ):
            raise ValueError("fin_reports: build_interval_minutes and reports_per_run must be positive")
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
            raise ValueError("turnover.timezone must be Europe/Moscow: WB statistics dates are always Moscow time")
        if self.telegram.poll_timeout_seconds < 1:
            raise ValueError("telegram.poll_timeout_seconds must be positive")
        if self.telegram.request_timeout_seconds <= self.telegram.poll_timeout_seconds:
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
        self._validate_screenshot_control()

    def _validate_screenshot_control(self) -> None:
        screens = self.screenshot_control
        if screens.mode not in ("off", "shadow", "enforce"):
            raise ValueError("screenshot_control.mode must be off, shadow or enforce")
        if screens.kpi_from:
            try:
                date.fromisoformat(screens.kpi_from)
            except ValueError as error:
                raise ValueError("screenshot_control.kpi_from must be a YYYY-MM-DD date or empty") from error
        try:
            ZoneInfo(screens.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("screenshot_control.timezone must be a known timezone") from error
        if (
            min(
                screens.poll_seconds,
                screens.max_upload_bytes,
                screens.min_side_px,
                screens.max_image_pixels,
                screens.retention_original_days,
                screens.retention_thumb_days,
                screens.retention_fields_days,
                screens.dedupe_window_days,
                screens.outcome_ttl_hours,
                screens.late_upload_hours,
                screens.clock_skew_minutes,
                screens.review_age_max_days,
                screens.burst_per_hour,
                screens.uploads_per_minute,
                screens.uploads_per_hour,
                screens.status_polls_per_minute,
                screens.ip_requests_per_minute,
                screens.max_extension_installs,
                screens.extraction_max_attempts,
                screens.image_url_ttl_seconds,
                screens.pairing_ttl_minutes,
                screens.pair_attempts_per_window,
                screens.pair_window_seconds,
                screens.extension_token_idle_days,
                screens.link_token_ttl_minutes,
                screens.staging_stale_minutes,
                screens.staging_max_bytes,
            )
            < 1
        ):
            raise ValueError("screenshot_control: limits, retention days and intervals must be positive")
        if screens.retention_fields_days < max(screens.retention_original_days, screens.retention_thumb_days):
            raise ValueError("screenshot_control.retention_fields_days must cover the image retention")
        if screens.image_url_ttl_seconds > 3600:
            raise ValueError("screenshot_control.image_url_ttl_seconds must not exceed an hour")
        if not 1 <= screens.staging_max_used_percent <= 100:
            raise ValueError("screenshot_control.staging_max_used_percent must be between 1 and 100")
        if not 0 <= screens.purge_hour <= 23 or not 0 <= screens.purge_minute <= 59:
            raise ValueError("screenshot_control.purge_hour and purge_minute must form a valid time")
        if not 0 <= screens.audit_percent <= 100:
            raise ValueError("screenshot_control.audit_percent must be between 0 and 100")
        limits = screens.thresholds
        if not all(0 < value <= 1 for value in (limits.text_dup, limits.text_maybe, limits.vlm_ocr_text)):
            raise ValueError("screenshot_control.thresholds text similarities must be in (0, 1]")
        if not all(0 < value <= 1 for value in (limits.near_tile_strict, limits.near_tile_soft)):
            raise ValueError("screenshot_control.thresholds tile similarities must be in (0, 1]")
        if limits.text_maybe > limits.text_dup:
            raise ValueError("screenshot_control.thresholds.text_maybe must not exceed text_dup")
        if not 0 <= limits.near_dhash_strict <= limits.near_dhash_soft <= 64:
            raise ValueError("screenshot_control.thresholds: near_dhash_strict <= near_dhash_soft <= 64")
        if limits.min_text_len < 1:
            raise ValueError("screenshot_control.thresholds.min_text_len must be positive")
        if re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,4}", screens.extension_min_version) is None:
            raise ValueError("screenshot_control.extension_min_version must look like 1.2.3")
        if not screens.allowed_url_prefixes or not all(
            prefix.startswith("https://") for prefix in screens.allowed_url_prefixes
        ):
            raise ValueError("screenshot_control.allowed_url_prefixes must be https:// prefixes")
        if not screens.notification_bot:
            raise ValueError("screenshot_control.notification_bot must be set")
        if self.vision.timeout_seconds < 1 or self.vision.max_tokens < 1 or self.vision.max_image_side < 1:
            raise ValueError("vision.timeout_seconds, max_tokens and max_image_side must be positive")

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
        # Ключ HMAC скриншотов здесь не проверяется: без него выключается только сам модуль
        # (`_screenshot_control`), а api и остальные воркеры стартуют.
        if invalid:
            raise ValueError(f"Unsafe or missing production settings: {', '.join(invalid)}")


_SCREENSHOT_TEXT_KEYS = frozenset(
    {
        "mode",
        "kpi_from",
        "timezone",
        "hmac_key",
        "s3_bucket",
        "staging_root",
        "notification_bot",
        "pipeline_version",
        "public_base_url",
        "extension_download_path",
        "extension_min_version",
    }
)
_SCREENSHOT_THRESHOLD_INTS = frozenset({"min_text_len", "near_dhash_strict", "near_dhash_soft"})


def _screenshot_control(raw: dict[str, Any]) -> ScreenshotControlConfig:
    config: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "thresholds":
            thresholds: dict[str, Any] = {
                name: int(item) if name in _SCREENSHOT_THRESHOLD_INTS else float(item)
                for name, item in dict(value or {}).items()
            }
            config[key] = ScreenshotThresholds(**thresholds)
        elif key == "allowed_url_prefixes":
            config[key] = tuple(str(prefix) for prefix in value)
        elif key == "telegram_enabled":
            config[key] = _as_bool(value)
        elif key == "audit_percent":
            config[key] = float(value)
        elif key in _SCREENSHOT_TEXT_KEYS:
            config[key] = "" if value is None else str(value)
        else:
            config[key] = int(value)
    return without_weak_hmac_key(ScreenshotControlConfig(**config))


SCREENSHOT_HMAC_MIN_LENGTH = 32


def without_weak_hmac_key(screens: ScreenshotControlConfig) -> ScreenshotControlConfig:
    """Без ключа HMAC (32+ символов) модуль скриншотов работать не должен: имена покупателей пошли бы
    в базу хешем без секрета. Раньше это роняло api и все воркеры; теперь выключается только модуль —
    эффективный режим `off` с причиной, о которой процессы пишут ERROR при старте, а HTTP модуля
    отвечает 503 `module_off`."""
    if screens.mode == "off" or len(screens.hmac_key) >= SCREENSHOT_HMAC_MIN_LENGTH:
        return screens
    return replace_fields(screens, mode="off", disabled_reason="hmac_key_missing", requested_mode=screens.mode)


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
