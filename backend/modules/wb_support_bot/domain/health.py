"""Состояние бота поддержки: проверки по снимку данных. Чистые функции, без базы и сети."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Severity(StrEnum):
    OK = "ok"
    WARN = "warn"
    CRIT = "crit"
    NODATA = "nodata"


GROUP_DELIVERY = "delivery"
GROUP_MODEL = "model"
GROUP_SAFETY = "safety"
GROUP_PANEL = "panel"

ENGINE_PANEL = "panel"

_RANK = {Severity.NODATA: 0, Severity.OK: 1, Severity.WARN: 2, Severity.CRIT: 3}


@dataclass(frozen=True, slots=True)
class Thresholds:
    worker_stale: timedelta = timedelta(minutes=30)
    mirror_stale: timedelta = timedelta(minutes=90)
    send_problems_warn: int = 5
    send_problems_crit: int = 20
    unknown_stuck: timedelta = timedelta(minutes=60)
    verdict_stuck: timedelta = timedelta(minutes=30)
    reply_stuck: timedelta = timedelta(minutes=60)
    model_bad_warn: float = 0.05
    model_bad_crit: float = 0.10
    min_sample: int = 10
    rate_limit_warn: int = 3
    decision_median_warn: int = 45
    calls_per_day_warn: int = 300
    outbox_stuck_count: int = 20
    outbox_stuck_age: timedelta = timedelta(minutes=10)
    late_warn: float = 0.05
    late_crit: float = 0.10
    late_tolerance: timedelta = timedelta(minutes=35)
    escalation_wait: timedelta = timedelta(hours=4)
    escalation_crit_count: int = 3
    escalated_after_continue_warn: float = 0.10
    continues_warn: int = 5
    # Панель решений: пороги по замеру на проде 05.11.2026 (настройка support_bot.panel).
    panel_window_days: int = 7
    panel_min_sample: int = 20
    panel_disagree_warn: float = 0.15
    panel_disagree_crit: float = 0.25
    panel_arbiter_warn: float = 0.20
    panel_arbiter_crit: float = 0.30
    panel_reserve_warn: float = 0.05
    panel_reserve_crit: float = 0.20
    panel_degraded_warn: int = 1
    panel_degraded_crit: int = 3


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Всё, что нужно проверкам. `None` вместо числа — данных нет (проверка получает `nodata`)."""

    now: datetime
    worker_checked_at: datetime | None
    check_worker: bool
    mode: str
    paused_reason: str | None
    globally_paused: bool
    mirror_read_through: datetime | None
    mirror_error: str | None
    sends_failed_24h: int
    sends_unknown_24h: int
    unknown_stuck: int
    sent_today: int
    daily_cap: int
    active_templates: int
    verdicts_pending_stuck: int
    reply_gate_mode: str
    reply_stuck: int
    reply_asked_24h: int
    reply_bad_24h: int
    model_auth_errors_24h: int
    model_rate_limited_24h: int
    median_decision_minutes: int | None
    calls_24h: int
    outbox_stuck: int
    stop_dialogs: int
    late_dialogs: int
    open_escalations_overdue: int
    continue_dialogs: int
    escalated_after_continue: int
    max_continues: int
    # Панель решений (sb007): всё посчитано по срезам окна панели, пути — пары «путь, сколько».
    reply_engine: str = "single"
    panel_rows: int = 0
    panel_decided: int = 0
    panel_paths: tuple[tuple[str, int], ...] = ()
    panel_judged: int = 0
    panel_disagreed: int = 0
    panel_arbiter: int = 0
    panel_reserve: int = 0
    panel_degraded: int = 0


@dataclass(frozen=True, slots=True)
class Check:
    id: str
    group: str
    title: str
    severity: Severity
    value: str | None
    threshold: str
    hint: str


def worst(severities: list[Severity]) -> Severity:
    """Худший из уровней; «мало данных» общий статус не портит и не улучшает."""
    real = [item for item in severities if item is not Severity.NODATA]
    return max(real, key=_RANK.__getitem__, default=Severity.OK)


def _percent(part: int, whole: int) -> str:
    return f"{part * 100 / whole:.1f}%" if whole else "—"


def _share_level(part: int, whole: int, warn: float, crit: float, min_sample: int) -> Severity:
    if whole < min_sample:
        return Severity.NODATA
    share = part / whole
    if share >= crit:
        return Severity.CRIT
    if share >= warn:
        return Severity.WARN
    return Severity.OK


def _age_minutes(now: datetime, moment: datetime) -> int:
    return max(0, int((now - moment).total_seconds() // 60))


def _limit(share: float) -> str:
    return f"{share * 100:.0f}%"


PATH_LABEL = {
    "agree": "судьи согласны",
    "arbiter": "арбитр",
    "reserve": "резерв",
    "degraded": "без решения",
    "guard": "без модели",
}


def _count_level(count: int, warn: int, crit: int) -> Severity:
    if count >= crit:
        return Severity.CRIT
    if count >= warn:
        return Severity.WARN
    return Severity.OK


def panel_checks(s: Snapshot, limits: Thresholds) -> list[Check]:
    """Метрики панели решений: расхождение судей, арбитр, резерв, цена, «нужен менеджер», пути.

    Считаются по последнему состоянию среза: истории попыток нет, повтор упавшего диалога
    перезаписывает путь и голоса. Токены берутся из голосов, поэтому degraded тоже с ценой.
    """
    window = f"за {limits.panel_window_days} дн."
    if s.reply_gate_mode == "off" or s.reply_engine != ENGINE_PANEL:
        return [
            Check(
                "panel_engine",
                GROUP_PANEL,
                "Панель решений",
                Severity.NODATA,
                "слой ответов выключен" if s.reply_gate_mode == "off" else "выключена: по ответу решает одна модель",
                "включается SUPPORT_BOT_REPLY_ENGINE=panel",
                "Пока движок single, судей и арбитра нет, считать нечего.",
            )
        ]
    checks = [
        Check(
            "panel_disagreement",
            GROUP_PANEL,
            "Судьи сходятся по действию",
            _share_level(
                s.panel_disagreed,
                s.panel_judged,
                limits.panel_disagree_warn,
                limits.panel_disagree_crit,
                limits.panel_min_sample,
            ),
            f"расходятся {_percent(s.panel_disagreed, s.panel_judged)} "
            f"({s.panel_disagreed} из {s.panel_judged} решений, где ответили оба судьи, {window})",
            f"до {_limit(limits.panel_disagree_warn)} — норма, от {_limit(limits.panel_disagree_crit)} — проблема "
            f"(на проде 05.11 было 0%)",
            "Судьи спорят чаще обычного: каждое расхождение зовёт арбитра и стоит денег. Проверьте промпт судей. "
            "Решения, где один судья не ответил, сюда не попадают — они считаются в карточке арбитра.",
        ),
        Check(
            "panel_arbiter",
            GROUP_PANEL,
            "Арбитр нужен не часто",
            _share_level(
                s.panel_arbiter,
                s.panel_decided,
                limits.panel_arbiter_warn,
                limits.panel_arbiter_crit,
                limits.panel_min_sample,
            ),
            f"{_percent(s.panel_arbiter, s.panel_decided)} ({s.panel_arbiter} из {s.panel_decided} решений {window})",
            f"до {_limit(limits.panel_arbiter_warn)} — норма, от {_limit(limits.panel_arbiter_crit)} — проблема "
            f"(на проде 05.11 было 3%)",
            "Арбитра зовут и при расхождении судей, и когда судья не ответил: смотрите карточку расхождения "
            "и отказы Cloud.ru. Решения с молчащим судьёй считаются здесь, но не в карточке расхождения, "
            "поэтому знаменатель у этой карточки больше.",
        ),
        Check(
            "panel_fallback",
            GROUP_PANEL,
            "Резерв и диалоги без решения",
            worst(
                [
                    _share_level(
                        s.panel_reserve,
                        s.panel_decided,
                        limits.panel_reserve_warn,
                        limits.panel_reserve_crit,
                        limits.panel_min_sample,
                    ),
                    _count_level(s.panel_degraded, limits.panel_degraded_warn, limits.panel_degraded_crit),
                ]
            ),
            f"резерв {_percent(s.panel_reserve, s.panel_decided)} ({s.panel_reserve}), "
            f"без решения {s.panel_degraded} {window}",
            f"резерв до {_limit(limits.panel_reserve_warn)} (от {_limit(limits.panel_reserve_crit)} — проблема), "
            f"без решения от {limits.panel_degraded_warn} — внимание, от {limits.panel_degraded_crit} — проблема",
            "Резерв значит, что основной арбитр не ответил; «без решения» — диалог держится без отправки. "
            "Проверьте доступ к Cloud.ru и журнал консьюмера ответов.",
        ),
        Check(
            "panel_paths",
            GROUP_PANEL,
            "Пути решений",
            Severity.NODATA if not s.panel_rows else Severity.OK,
            ", ".join(
                f"{PATH_LABEL.get(path, path)} {_percent(count, s.panel_rows)} ({count})"
                for path, count in s.panel_paths
            )
            or None,
            f"без порога: смесь путей {window}",
            "Распределение по decision_path: согласие судей, арбитр, резерв, без решения, остановка без модели.",
        ),
    ]
    return checks


def evaluate(snapshot: Snapshot, limits: Thresholds = Thresholds()) -> list[Check]:
    s = snapshot
    checks: list[Check] = []

    def add(
        check_id: str, group: str, title: str, severity: Severity, value: str | None, threshold: str, hint: str
    ) -> None:
        checks.append(Check(check_id, group, title, severity, value, threshold, hint))

    # ---- рассылка ----
    if s.check_worker:
        if s.worker_checked_at is None:
            add(
                "worker_alive",
                GROUP_DELIVERY,
                "Воркер бота работает",
                Severity.NODATA,
                None,
                "проход не позже 30 минут назад",
                "Воркер ещё не записал ни одного прохода.",
            )
        else:
            age = _age_minutes(s.now, s.worker_checked_at)
            level = Severity.CRIT if s.now - s.worker_checked_at > limits.worker_stale else Severity.OK
            add(
                "worker_alive",
                GROUP_DELIVERY,
                "Воркер бота работает",
                level,
                f"последний проход {age} мин назад",
                "проход не позже 30 минут назад",
                "Проверьте контейнер wb-support-bot-worker и его журнал на сервере.",
            )

    if s.mirror_read_through is None:
        add(
            "mirror_fresh",
            GROUP_DELIVERY,
            "Зеркало чатов свежее",
            Severity.CRIT,
            "лента чатов не читалась",
            "не старше 90 минут",
            "Пока зеркало не читалось, бот ничего не отправляет. Смотрите сбор чатов в wb-core-worker.",
        )
    else:
        age = _age_minutes(s.now, s.mirror_read_through)
        level = Severity.CRIT if s.now - s.mirror_read_through > limits.mirror_stale else Severity.OK
        value = f"прочитано {age} мин назад" + (f", ошибка: {s.mirror_error}" if s.mirror_error else "")
        add(
            "mirror_fresh",
            GROUP_DELIVERY,
            "Зеркало чатов свежее",
            level,
            value,
            "не старше 90 минут",
            "Зеркало старше порога: бот не отправляет. Проверьте сбор чатов и доступ к WB.",
        )

    stopped = s.globally_paused or s.paused_reason is not None
    add(
        "sending_enabled",
        GROUP_DELIVERY,
        "Отправка не остановлена",
        Severity.WARN if stopped else Severity.OK,
        "общая пауза"
        if s.globally_paused
        else (f"кабинет остановлен: {s.paused_reason}" if s.paused_reason else "включена"),
        "без паузы и остановки кабинета",
        "Снимите паузу кнопкой на странице бота или исправьте причину остановки кабинета.",
    )

    problems = s.sends_failed_24h + s.sends_unknown_24h
    level = (
        Severity.CRIT
        if problems >= limits.send_problems_crit
        else Severity.WARN
        if problems >= limits.send_problems_warn
        else Severity.OK
    )
    add(
        "send_failures",
        GROUP_DELIVERY,
        "Отправки без ошибок за сутки",
        level,
        f"не ушло {s.sends_failed_24h}, неизвестно {s.sends_unknown_24h}",
        "до 5 — норма, от 20 — проблема",
        "Посмотрите журнал отправок: причины в колонке «Ошибка».",
    )

    add(
        "unknown_stuck",
        GROUP_DELIVERY,
        "Нет зависших «неизвестных» отправок",
        Severity.CRIT if s.unknown_stuck else Severity.OK,
        f"{s.unknown_stuck}",
        "0 дольше часа",
        "Статус «неизвестно» должен закрываться сверкой с лентой за 45 минут. Проверьте зеркало чатов.",
    )

    add(
        "daily_cap",
        GROUP_DELIVERY,
        "Суточный потолок не исчерпан",
        Severity.WARN if s.daily_cap and s.sent_today >= s.daily_cap else Severity.OK,
        f"{s.sent_today} из {s.daily_cap}",
        "ниже потолка",
        "Потолок исчерпан, остаток уйдёт завтра. Поднимите daily_cap_per_seller, если это штатно.",
    )

    if s.mode == "live":
        add(
            "templates",
            GROUP_DELIVERY,
            "Есть активные тексты",
            Severity.WARN if s.active_templates == 0 else Severity.OK,
            f"{s.active_templates}",
            "хотя бы один",
            "Без активных текстов бот ничего не отправляет. Включите текст на странице.",
        )

    # ---- нейросеть ----
    add(
        "verdict_pending",
        GROUP_MODEL,
        "Вердикты по менеджеру приходят вовремя",
        Severity.CRIT if s.verdicts_pending_stuck else Severity.OK,
        f"ждут дольше 30 минут: {s.verdicts_pending_stuck}",
        "0",
        "Проверьте консьюмер вердиктов, ключ и доступ к Cloud.ru.",
    )

    gate_on = s.reply_gate_mode != "off"
    if gate_on:
        add(
            "reply_stuck",
            GROUP_MODEL,
            "Ответы покупателей оцениваются вовремя",
            Severity.CRIT if s.reply_stuck else Severity.OK,
            f"диалогов в ожидании дольше часа: {s.reply_stuck}",
            "0",
            "Диалоги держатся без напоминаний. Проверьте консьюмер ответов, ключ и доступ к Cloud.ru.",
        )
        level = _share_level(
            s.reply_bad_24h, s.reply_asked_24h, limits.model_bad_warn, limits.model_bad_crit, limits.min_sample
        )
        add(
            "model_quality",
            GROUP_MODEL,
            "Модель отвечает по делу",
            level,
            f"«не понятно» и сбои: {_percent(s.reply_bad_24h, s.reply_asked_24h)} из {s.reply_asked_24h}",
            "до 5% — норма, от 10% — проблема",
            "Много «не понятно» или сбоев: посмотрите причины в карточках диалогов, проверьте промпт.",
        )
        add(
            "model_access",
            GROUP_MODEL,
            "Ключ и лимит Cloud.ru в порядке",
            Severity.CRIT
            if s.model_auth_errors_24h
            else Severity.WARN
            if s.model_rate_limited_24h >= limits.rate_limit_warn
            else Severity.OK,
            f"отказов доступа {s.model_auth_errors_24h}, лимитов (429) {s.model_rate_limited_24h}",
            "0 отказов доступа, лимитов меньше 3",
            "401/403: ключ отозван или без прав. 429: лимит запросов, запросы уходят по одному и повторяются.",
        )
        if s.median_decision_minutes is None:
            add(
                "decision_time",
                GROUP_MODEL,
                "Решение по ответу приходит быстро",
                Severity.NODATA,
                None,
                "медиана до 45 минут",
                "За сутки не было решений.",
            )
        else:
            add(
                "decision_time",
                GROUP_MODEL,
                "Решение по ответу приходит быстро",
                Severity.WARN if s.median_decision_minutes > limits.decision_median_warn else Severity.OK,
                f"медиана {s.median_decision_minutes} мин",
                "медиана до 45 минут (зеркало 30 + такт 10)",
                "Решения запаздывают: проверьте свежесть зеркала и нагрузку на консьюмер.",
            )
    add(
        "token_budget",
        GROUP_MODEL,
        "Расход нейросети в бюджете",
        Severity.WARN if s.calls_24h > limits.calls_per_day_warn else Severity.OK,
        f"вызовов за сутки: {s.calls_24h}",
        "до 300 в сутки",
        "Вызовов больше обычного: проверьте, не зациклились повторы.",
    )
    add(
        "outbox_queue",
        GROUP_MODEL,
        "Очередь запросов к модели не копится",
        Severity.CRIT if s.outbox_stuck >= limits.outbox_stuck_count else Severity.OK,
        f"неопубликовано дольше 10 минут: {s.outbox_stuck}",
        "меньше 20",
        "Очередь outbox не публикуется в Kafka. Проверьте outbox-publisher и Kafka.",
    )

    # ---- панель решений ----
    checks.extend(panel_checks(s, limits))

    # ---- безопасность слоя ответов ----
    if not gate_on:
        for check_id, title in (
            ("late_after_stop", "Сообщения бота после «стоп»"),
            ("escalations_open", "Эскалации без ответа менеджера"),
            ("escalation_after_continue", "Эскалации после «продолжаем»"),
            ("continues_per_dialog", "Продолжений на один диалог"),
        ):
            add(
                check_id,
                GROUP_SAFETY,
                title,
                Severity.NODATA,
                "слой ответов выключен",
                "—",
                "Включается переменной SUPPORT_BOT_REPLY_GATE_MODE.",
            )
        return checks

    level = _share_level(s.late_dialogs, s.stop_dialogs, limits.late_warn, limits.late_crit, limits.min_sample)
    add(
        "late_after_stop",
        GROUP_SAFETY,
        "Сообщения бота после «стоп»",
        level,
        f"{_percent(s.late_dialogs, s.stop_dialogs)} "
        f"({s.late_dialogs} из {s.stop_dialogs} остановленных диалогов за 7 дней)",
        "до 5% — норма, от 10% — проблема; допуск 35 минут задержки зеркала",
        "Бот писал после того, как рассылку нужно было остановить. Откройте эти диалоги и проверьте журнал отправок.",
    )
    level = (
        Severity.CRIT
        if s.open_escalations_overdue >= limits.escalation_crit_count
        else Severity.WARN
        if s.open_escalations_overdue
        else Severity.OK
    )
    add(
        "escalations_open",
        GROUP_SAFETY,
        "Эскалации без ответа менеджера",
        level,
        f"дольше 4 часов: {s.open_escalations_overdue}",
        "0; от 3 — проблема",
        "Угрозы и просьбы прекратить ждут менеджера. Откройте вкладку «Ответы», раздел «Эскалации».",
    )
    level = (
        Severity.NODATA
        if s.continue_dialogs < limits.min_sample
        else Severity.WARN
        if s.escalated_after_continue / s.continue_dialogs >= limits.escalated_after_continue_warn
        else Severity.OK
    )
    add(
        "escalation_after_continue",
        GROUP_SAFETY,
        "Эскалации после «продолжаем»",
        level,
        f"{_percent(s.escalated_after_continue, s.continue_dialogs)} "
        f"({s.escalated_after_continue} из {s.continue_dialogs} диалогов за 14 дней)",
        "до 10%",
        "После продолжения рассылки покупатели чаще угрожают или просят прекратить. Пересмотрите классы «продолжаем».",
    )
    add(
        "continues_per_dialog",
        GROUP_SAFETY,
        "Продолжений на один диалог",
        Severity.WARN if s.max_continues >= limits.continues_warn else Severity.OK,
        f"максимум {s.max_continues}",
        "меньше 5",
        "Покупатель отвечает снова и снова, а рассылка идёт. Посмотрите такие диалоги.",
    )
    return checks
