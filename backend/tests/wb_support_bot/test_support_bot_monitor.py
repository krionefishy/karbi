# ruff: noqa: F811 — фикстуры общие с тестами процессора и страницы и подставляются по имени.
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select

from backend.app.factory import Application
from backend.modules.wb_support_bot.application.monitor import (
    BotMonitor,
    history_days,
    monitor_limits,
    monitor_prices,
)
from backend.modules.wb_support_bot.domain.health import Severity, Snapshot, Thresholds, evaluate, worst
from backend.modules.wb_support_bot.infrastructure.postgres import (
    MonitorEventModel,
    MonitorStateModel,
    ReplyVerdictModel,
    SupportBotRepository,
)
from backend.shared.settings.config import PanelMonitorConfig
from backend.storage.pg import Database
from backend.tests.wb_support_bot.test_support_bot_http import API, AUTOMATION, application, client  # noqa: F401
from backend.tests.wb_support_bot.test_support_bot_http import seller as http_seller  # noqa: F401
from backend.tests.wb_support_bot.test_support_bot_processor import (  # noqa: F401
    BASE_FEED,
    CHAT,
    NOW,
    PROMPT_AT,
    database,
    feed,
    seller,
)

LIMITS = Thresholds()


def healthy(**changes: object) -> Snapshot:
    snapshot = Snapshot(
        now=NOW,
        worker_checked_at=NOW - timedelta(minutes=5),
        check_worker=True,
        mode="live",
        paused_reason=None,
        globally_paused=False,
        mirror_read_through=NOW - timedelta(minutes=10),
        mirror_error=None,
        sends_failed_24h=0,
        sends_unknown_24h=0,
        unknown_stuck=0,
        sent_today=100,
        daily_cap=2500,
        active_templates=3,
        verdicts_pending_stuck=0,
        reply_gate_mode="enforce",
        reply_stuck=0,
        reply_asked_24h=40,
        reply_bad_24h=0,
        model_auth_errors_24h=0,
        model_rate_limited_24h=0,
        median_decision_minutes=20,
        calls_24h=40,
        outbox_stuck=0,
        stop_dialogs=30,
        late_dialogs=0,
        open_escalations_overdue=0,
        continue_dialogs=20,
        escalated_after_continue=0,
        max_continues=1,
        reply_engine="panel",
        panel_rows=104,
        panel_decided=100,
        panel_paths=(("agree", 84), ("arbiter", 15), ("guard", 4), ("reserve", 1)),
        panel_judged=100,
        panel_disagreed=16,
        panel_arbiter=15,
        panel_reserve=1,
        panel_degraded=0,
        panel_needs_known=100,
        panel_needs_manager=60,
        panel_costed=100,
        panel_cost_rub=3.0,
        panel_unpriced_models=(),
    )
    return replace(snapshot, **changes)  # type: ignore[arg-type]


def level(snapshot: Snapshot, check_id: str) -> Severity:
    return next(check.severity for check in evaluate(snapshot, LIMITS) if check.id == check_id)


def test_a_healthy_bot_has_every_check_green() -> None:
    checks = evaluate(healthy(), LIMITS)

    assert worst([check.severity for check in checks]) is Severity.OK
    assert {check.severity for check in checks} == {Severity.OK}
    assert {check.group for check in checks} == {"delivery", "model", "panel", "safety"}


def test_the_worker_and_the_mirror_go_red_when_stale() -> None:
    assert level(healthy(worker_checked_at=NOW - timedelta(minutes=31)), "worker_alive") is Severity.CRIT
    assert level(healthy(worker_checked_at=None), "worker_alive") is Severity.NODATA
    assert level(healthy(mirror_read_through=NOW - timedelta(minutes=91)), "mirror_fresh") is Severity.CRIT
    assert level(healthy(mirror_read_through=None), "mirror_fresh") is Severity.CRIT
    assert "worker_alive" not in {check.id for check in evaluate(healthy(check_worker=False), LIMITS)}


def test_a_pause_is_a_warning_not_an_outage() -> None:
    assert level(healthy(globally_paused=True), "sending_enabled") is Severity.WARN
    assert level(healthy(paused_reason="ключ без права записи"), "sending_enabled") is Severity.WARN


def test_send_problems_have_two_levels() -> None:
    assert level(healthy(sends_failed_24h=4), "send_failures") is Severity.OK
    assert level(healthy(sends_failed_24h=3, sends_unknown_24h=2), "send_failures") is Severity.WARN
    assert level(healthy(sends_failed_24h=20), "send_failures") is Severity.CRIT
    assert level(healthy(unknown_stuck=1), "unknown_stuck") is Severity.CRIT


def test_model_quality_is_a_share_with_a_minimum_sample() -> None:
    assert level(healthy(reply_asked_24h=9, reply_bad_24h=9), "model_quality") is Severity.NODATA
    assert level(healthy(reply_asked_24h=100, reply_bad_24h=4), "model_quality") is Severity.OK
    assert level(healthy(reply_asked_24h=100, reply_bad_24h=5), "model_quality") is Severity.WARN
    assert level(healthy(reply_asked_24h=100, reply_bad_24h=10), "model_quality") is Severity.CRIT


def test_access_errors_are_red_and_rate_limits_are_a_warning() -> None:
    assert level(healthy(model_auth_errors_24h=1), "model_access") is Severity.CRIT
    assert level(healthy(model_rate_limited_24h=3), "model_access") is Severity.WARN
    assert level(healthy(model_rate_limited_24h=2), "model_access") is Severity.OK


def test_stuck_work_is_red() -> None:
    assert level(healthy(reply_stuck=1), "reply_stuck") is Severity.CRIT
    assert level(healthy(verdicts_pending_stuck=1), "verdict_pending") is Severity.CRIT
    assert level(healthy(outbox_stuck=20), "outbox_queue") is Severity.CRIT
    assert level(healthy(outbox_stuck=19), "outbox_queue") is Severity.OK


def test_sends_after_a_stop_are_judged_as_a_share() -> None:
    assert level(healthy(stop_dialogs=9, late_dialogs=9), "late_after_stop") is Severity.NODATA
    assert level(healthy(stop_dialogs=100, late_dialogs=4), "late_after_stop") is Severity.OK
    assert level(healthy(stop_dialogs=100, late_dialogs=5), "late_after_stop") is Severity.WARN
    assert level(healthy(stop_dialogs=100, late_dialogs=10), "late_after_stop") is Severity.CRIT


def test_overdue_escalations_and_repeated_continues_are_flagged() -> None:
    assert level(healthy(open_escalations_overdue=1), "escalations_open") is Severity.WARN
    assert level(healthy(open_escalations_overdue=3), "escalations_open") is Severity.CRIT
    assert level(healthy(continue_dialogs=20, escalated_after_continue=2), "escalation_after_continue") is Severity.WARN
    assert (
        level(healthy(continue_dialogs=5, escalated_after_continue=5), "escalation_after_continue") is Severity.NODATA
    )
    assert level(healthy(max_continues=5), "continues_per_dialog") is Severity.WARN


def test_with_the_reply_layer_off_the_safety_checks_have_no_data() -> None:
    checks = evaluate(healthy(reply_gate_mode="off"), LIMITS)

    safety = [check for check in checks if check.group == "safety"]
    assert safety and {check.severity for check in safety} == {Severity.NODATA}
    assert "reply_stuck" not in {check.id for check in checks}
    assert worst([check.severity for check in checks]) is Severity.OK


def test_judges_disagreeing_too_often_is_a_warning_then_a_problem() -> None:
    assert level(healthy(panel_judged=100, panel_disagreed=22), "panel_disagreement") is Severity.OK
    assert level(healthy(panel_judged=100, panel_disagreed=30), "panel_disagreement") is Severity.WARN
    assert level(healthy(panel_judged=100, panel_disagreed=45), "panel_disagreement") is Severity.CRIT
    assert level(healthy(panel_judged=19, panel_disagreed=19), "panel_disagreement") is Severity.NODATA


def test_arbiter_calls_are_judged_as_a_share_of_decisions() -> None:
    assert level(healthy(panel_decided=100, panel_arbiter=21), "panel_arbiter") is Severity.OK
    assert level(healthy(panel_decided=100, panel_arbiter=35), "panel_arbiter") is Severity.WARN
    assert level(healthy(panel_decided=100, panel_arbiter=50), "panel_arbiter") is Severity.CRIT


def test_the_reserve_and_held_dialogs_share_one_card() -> None:
    assert level(healthy(panel_reserve=4), "panel_fallback") is Severity.OK
    assert level(healthy(panel_reserve=5), "panel_fallback") is Severity.WARN
    assert level(healthy(panel_reserve=20), "panel_fallback") is Severity.CRIT
    assert level(healthy(panel_degraded=1), "panel_fallback") is Severity.WARN
    assert level(healthy(panel_degraded=3), "panel_fallback") is Severity.CRIT


def test_the_price_of_a_decision_has_two_levels_and_needs_prices() -> None:
    assert level(healthy(panel_cost_rub=5.9), "panel_cost") is Severity.OK
    assert level(healthy(panel_cost_rub=6.0), "panel_cost") is Severity.WARN
    assert level(healthy(panel_cost_rub=12.0), "panel_cost") is Severity.CRIT
    assert level(healthy(panel_costed=19), "panel_cost") is Severity.NODATA
    unpriced = healthy(panel_unpriced_models=("z-ai/glm-4.5-air",))
    card = next(check for check in evaluate(unpriced, LIMITS) if check.id == "panel_cost")
    assert card.severity is Severity.NODATA and "glm-4.5-air" in (card.value or "")


def test_the_needs_manager_flag_warns_only_at_the_edges() -> None:
    assert level(healthy(panel_needs_manager=20), "panel_needs_manager") is Severity.OK
    assert level(healthy(panel_needs_manager=19), "panel_needs_manager") is Severity.WARN
    assert level(healthy(panel_needs_manager=91), "panel_needs_manager") is Severity.WARN
    assert level(healthy(panel_needs_known=19, panel_needs_manager=19), "panel_needs_manager") is Severity.NODATA


def test_the_paths_card_only_shows_the_mix() -> None:
    card = next(check for check in evaluate(healthy(), LIMITS) if check.id == "panel_paths")

    assert card.severity is Severity.OK
    assert card.value is not None and "судьи согласны 80.8% (84)" in card.value and "арбитр" in card.value
    assert level(healthy(panel_rows=0, panel_paths=()), "panel_paths") is Severity.NODATA


def test_without_the_panel_the_group_has_one_card_with_no_data() -> None:
    for snapshot in (healthy(reply_engine="single"), healthy(reply_gate_mode="off")):
        panel = [check for check in evaluate(snapshot, LIMITS) if check.group == "panel"]
        assert [check.id for check in panel] == ["panel_engine"]
        assert panel[0].severity is Severity.NODATA
    assert worst([check.severity for check in evaluate(healthy(reply_engine="single"), LIMITS)]) is Severity.OK


def test_panel_thresholds_come_from_the_settings() -> None:
    panel = PanelMonitorConfig(
        disagree_warn_percent=50, disagree_crit_percent=70, cost_warn_kopecks=20, cost_crit_kopecks=30, min_sample=5
    )
    limits = monitor_limits(panel)

    assert limits.panel_disagree_warn == 0.5 and limits.panel_cost_warn == 0.2
    assert limits.panel_min_sample == 5 and limits.worker_stale == Thresholds().worker_stale
    loose = healthy(panel_judged=100, panel_disagreed=55)
    assert level(loose, "panel_disagreement") is Severity.CRIT, "по умолчанию 55% — красный"
    assert (
        next(check for check in evaluate(loose, limits) if check.id == "panel_disagreement").severity is Severity.WARN
    )
    assert monitor_prices(PanelMonitorConfig())["Qwen/Qwen3-30B-A3B"] == (16, 65)


def test_the_overall_level_is_the_worst_one_and_no_data_does_not_count() -> None:
    assert worst([Severity.OK, Severity.WARN, Severity.NODATA]) is Severity.WARN
    assert worst([Severity.WARN, Severity.CRIT]) is Severity.CRIT
    assert worst([Severity.NODATA]) is Severity.OK


def event(check: str, severity: str, start: datetime, end: datetime | None) -> MonitorEventModel:
    return MonitorEventModel(
        seller_id=uuid.uuid4(), check_id=check, title="t", severity=severity, started_at=start, ended_at=end
    )


def test_history_splits_an_incident_across_moscow_days() -> None:
    now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
    # 28.10 23:00 МСК — 29.10 01:00 МСК: по часу в каждые сутки.
    crossing = event("a", "crit", datetime(2026, 10, 28, 20, 0, tzinfo=UTC), datetime(2026, 10, 28, 22, 0, tzinfo=UTC))
    open_one = event("b", "warn", now - timedelta(minutes=40), None)

    days = history_days([crossing, open_one], now)

    by_date = {day.date: day for day in days}
    assert len(days) == 7 and days[-1].date == "2026-10-29"
    assert (by_date["2026-10-28"].crit_minutes, by_date["2026-10-29"].crit_minutes) == (60, 60)
    assert by_date["2026-10-29"].warn_minutes == 40


async def test_recording_opens_closes_and_updates_incidents(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, BASE_FEED, read_at=NOW)

    async def record(moment: datetime) -> Severity:
        async with database.session() as session:
            monitor = BotMonitor(
                session,
                SupportBotRepository(session),
                daily_cap=2500,
                reply_gate_mode="enforce",
                reply_prompt_version="r3",
            )
            return await monitor.record(seller, moment)

    async def events() -> list[MonitorEventModel]:
        async with database.session() as session:
            return list(
                await session.scalars(
                    select(MonitorEventModel)
                    .where(MonitorEventModel.seller_id == seller)
                    .order_by(MonitorEventModel.id)
                )
            )

    # Зеркало прочитано в NOW; через 2 часа оно устарело (красный), а нет активных текстов в live — отдельная жёлтая.
    fresh = await record(NOW + timedelta(minutes=5))
    assert fresh is Severity.OK and await events() == []
    stale_at = NOW + timedelta(hours=2)
    assert await record(stale_at) is Severity.CRIT
    first = await events()
    assert [(item.check_id, item.severity, item.ended_at) for item in first] == [("mirror_fresh", "crit", None)]
    again = await record(stale_at + timedelta(minutes=10))
    assert again is Severity.CRIT and len(await events()) == 1, "та же проблема не открывает второй период"

    await feed(database, seller, BASE_FEED, read_at=NOW + timedelta(hours=2, minutes=15))
    assert await record(NOW + timedelta(hours=2, minutes=20)) is Severity.OK
    closed = await events()
    assert len(closed) == 1 and closed[0].ended_at is not None

    async with database.session() as session:
        state = await session.get(MonitorStateModel, seller)
        assert state is not None and state.overall == "ok" and state.checks


async def test_panel_metrics_are_read_from_the_votes_in_the_database(database: Database, seller: uuid.UUID) -> None:
    """Подставные срезы панели: монитор должен посчитать расхождение, арбитра и цену по JSON голосов."""
    await feed(database, seller, BASE_FEED, read_at=NOW)
    judge_a = {"role": "judge_a", "model": "judge-a", "action": "continue", "tokens_in": 1000, "tokens_out": 100}
    judge_b = {"role": "judge_b", "model": "judge-b", "action": "stop", "tokens_in": 1000, "tokens_out": 100}
    async with database.session() as session:
        for index, (path, votes, needs) in enumerate(
            (
                ("agree", [judge_a, {**judge_b, "action": "continue"}], False),
                ("arbiter", [judge_a, judge_b, {**judge_b, "role": "arbiter", "model": "arbiter"}], True),
                ("degraded", [judge_a, judge_b], True),
            )
        ):
            at = NOW - timedelta(hours=index + 1)
            session.add(
                ReplyVerdictModel(
                    id=uuid.uuid4(),
                    seller_id=seller,
                    chat_id=CHAT,
                    prompt_at=PROMPT_AT,
                    slice_fingerprint=f"{index:040d}",
                    first_event_id=f"reply-{index}",
                    last_event_id=f"reply-{index}",
                    first_event_at=at,
                    last_event_at=at,
                    messages=1,
                    prompt_version="r4",
                    status="done" if path != "degraded" else "failed",
                    category="noise",
                    needs_manager=needs,
                    decision_path=path,
                    decision_votes={"policy_version": "r4", "rules_applied": [], "votes": votes},
                    attempts=1,
                    requested_at=at,
                    completed_at=at + timedelta(minutes=2),
                )
            )
        await session.commit()

    async with database.session() as session:
        monitor = BotMonitor(
            session,
            SupportBotRepository(session),
            daily_cap=2500,
            reply_gate_mode="enforce",
            reply_prompt_version="r4",
            reply_engine="panel",
            prices={"judge-a": (16, 65), "judge-b": (16, 61), "arbiter": (43, 87)},
        )
        snapshot = await monitor.snapshot(seller, NOW, worker_checked_at=NOW, check_worker=False)

    assert (snapshot.panel_rows, snapshot.panel_decided) == (3, 3)
    assert (snapshot.panel_judged, snapshot.panel_disagreed) == (3, 2)
    assert (snapshot.panel_arbiter, snapshot.panel_degraded) == (2, 1)
    assert (snapshot.panel_needs_known, snapshot.panel_needs_manager) == (3, 2)
    judges = ((1000 * 16 + 100 * 65) + (1000 * 16 + 100 * 61)) / 1_000_000
    arbiter = (1000 * 43 + 100 * 87) / 1_000_000
    assert snapshot.panel_costed == 3 and round(snapshot.panel_cost_rub, 6) == round(3 * judges + arbiter, 6)
    cards = {check.id: check for check in evaluate(snapshot, LIMITS) if check.group == "panel"}
    assert set(cards) == {
        "panel_disagreement",
        "panel_arbiter",
        "panel_fallback",
        "panel_cost",
        "panel_needs_manager",
        "panel_paths",
    }
    assert cards["panel_fallback"].severity is Severity.WARN, "один диалог без решения — внимание"
    assert cards["panel_disagreement"].severity is Severity.NODATA, "трёх решений для доли мало"


async def test_the_monitor_page_shows_checks_and_history(
    application: Application, client: AsyncClient, http_seller: uuid.UUID
) -> None:
    assert (await client.post(AUTOMATION, json={"seller_id": str(http_seller)})).status_code == 201
    async with application.database.session() as session:
        monitor = BotMonitor(
            session, SupportBotRepository(session), daily_cap=2500, reply_gate_mode="enforce", reply_prompt_version="r3"
        )
        await monitor.record(http_seller, datetime.now(UTC))

    body = (await client.get(f"{API}/sellers/{http_seller}/monitor")).json()

    assert body["worker_checked_at"] is not None and body["overall"] in {"ok", "warn", "crit"}
    ids = {check["id"] for check in body["checks"]}
    assert {"worker_alive", "mirror_fresh", "send_failures", "late_after_stop"} <= ids
    assert len(body["days"]) == 7 and body["days"][-1]["crit_minutes"] >= 0
    page = (await client.get(f"{API}/sellers/{http_seller}")).json()
    assert page["monitor_overall"] in {"ok", "warn", "crit"}


async def test_a_page_without_any_recording_says_no_data(client: AsyncClient, http_seller: uuid.UUID) -> None:
    assert (await client.post(AUTOMATION, json={"seller_id": str(http_seller)})).status_code == 201

    body = (await client.get(f"{API}/sellers/{http_seller}")).json()
    monitor = (await client.get(f"{API}/sellers/{http_seller}/monitor")).json()

    assert body["monitor_overall"] == "nodata"
    assert next(item for item in monitor["checks"] if item["id"] == "worker_alive")["severity"] == "nodata"
    assert (await client.get(f"{API}/sellers/{uuid.uuid4()}/monitor")).status_code == 404
