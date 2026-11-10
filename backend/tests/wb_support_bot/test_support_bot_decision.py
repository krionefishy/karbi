# ruff: noqa: F811 — фикстуры `database` и `seller` общие с тестами процессора и подставляются по имени.
import json
import re
import uuid
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

import pytest

from backend.modules.wb_support_bot.application.decision import DecisionEngine, PanelSpec, RoleSpec
from backend.modules.wb_support_bot.domain import Gate
from backend.modules.wb_support_bot.domain.decision import (
    PATH_AGREE,
    PATH_ARBITER,
    PATH_DEGRADED,
    PATH_GUARD,
    PATH_RESERVE,
    ROLE_BOT,
    ROLE_BUYER,
    ROLE_SELLER,
    RULE_ARBITER_NEITHER,
    RULE_CONFLICT_STOP,
    RULE_NOISE_AFTER_MANAGER,
    Vote,
    last_reply_is_from_manager,
    manager_message_times,
    manager_replied_before,
    parse_arbiter_choice,
    parse_panel_verdict,
    swap_candidates,
    vote_action,
)
from backend.modules.wb_support_bot.domain.reply import reply_user_message
from backend.modules.wb_support_bot.infrastructure.postgres import VERDICT_DONE, VERDICT_FAILED
from backend.shared.llm import LLMAnswerError, LLMClient, LLMError, LLMResult, LLMTransientError
from backend.shared.settings.config import LLMConfig, SupportBotConfig
from backend.storage.pg import Database
from backend.tests.wb_support_bot.test_support_bot_processor import (  # noqa: F401
    BASE_FEED,
    NOW,
    PROMPT_AT,
    database,
    event,
    feed,
    seller,
)
from backend.tests.wb_support_bot.test_support_bot_reply_gate import (
    REPLY,
    FakeLLM,
    deliver,
    request_reply,
    stored,
)
from backend.workers.wb_support_bot.reply_consumer import ReplyConsumer

QWEN, GPT, ARBITER, RESERVE = "qwen-a", "gpt-b", "arbiter-c", "reserve-d"
SPEC = PanelSpec(
    judge_a=RoleSpec(QWEN, 150),
    judge_b=RoleSpec(GPT, 400, {"reasoning_effort": "low"}),
    arbiter=RoleSpec(ARBITER, 1500),
    reserve=RoleSpec(RESERVE, 1500),
    policy_version="d1",
)
MANAGER_CONTEXT = [(ROLE_BOT, "Напоминание"), (ROLE_SELLER, "Выберите вариант 1 или 2")]
BOT_CONTEXT = [(ROLE_SELLER, "Выберите вариант 1 или 2"), (ROLE_BOT, "Напоминание")]
Answer = str | Exception | Callable[[str], str]


def verdict(category: str, needs_manager: bool | str = False, reason: str = "причина") -> str:
    return json.dumps({"category": category, "needs_manager": needs_manager, "reason": reason}, ensure_ascii=False)


def choice(label: str) -> str:
    return f'Рассуждаю коротко.\n{{"choice": "{label}", "reason": "так точнее"}}'


def prefer(category: str) -> Callable[[str], str]:
    """Арбитр выбирает вердикт с нужным классом, в каком бы порядке их ни показали."""

    def pick(user: str) -> str:
        shown = dict(re.findall(r"Вердикт ([AB]): класс (\w+)", user))
        return choice(next(label for label, value in shown.items() if value == category))

    return pick


class PanelLLM:
    def __init__(self, answers: Mapping[str, Answer]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str, str, int | None, Mapping[str, Any] | None]] = []

    async def complete(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> LLMResult:
        assert model is not None
        self.calls.append((model, system, user, max_tokens, extra_body))
        answer = self.answers[model]
        if isinstance(answer, Exception):
            raise answer
        text = answer(user) if callable(answer) else answer
        return LLMResult(text, model, 1000, 40, 300)

    def models(self) -> list[str]:
        return [call[0] for call in self.calls]


def engine(answers: Mapping[str, Answer]) -> tuple[DecisionEngine, PanelLLM]:
    llm = PanelLLM(answers)
    return DecisionEngine(llm, SPEC), llm


async def decide(machine: DecisionEngine, *, context: list[tuple[str, str]] | None = None, seed: str = "s"):  # type: ignore[no-untyped-def]
    return await machine.decide(context if context is not None else BOT_CONTEXT, ["Второй"], seed=seed)


# ---- разбор ответов ----


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"category": "refuses", "needs_manager": false, "reason": "не хочет"}', ("refuses", False, "не хочет")),
        ('{"category": "question", "needs_manager": true, "reason": "ждёт"}', ("question", True, "ждёт")),
        ('{"category": "noise", "needs_manager": "Да", "reason": "x"}', ("noise", True, "x")),
        ('{"category": "noise", "reason": "x"}', ("noise", None, "x")),
        ('```json\n{"category": "thanks", "needs_manager": "нет", "reason": "r"}\n```', ("thanks", False, "r")),
        ("не json", ("unclear", None, "ответ модели без JSON")),
        ('{"category": "что-то", "needs_manager": false}', ("unclear", False, "неизвестный класс модели")),
    ],
)
def test_the_panel_verdict_has_the_class_the_flag_and_the_reason(
    raw: str, expected: tuple[str, bool | None, str]
) -> None:
    assert parse_panel_verdict(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (choice("A"), ("A", "так точнее")),
        ('{"choice": "b", "reason": "r"}', ("B", "r")),
        ('Думаю {"choice": "A"} но потом {"choice": "neither", "reason": "оба мимо"}', ("neither", "оба мимо")),
        ('{"choice": "C"}', None),
        ("нет выбора", None),
    ],
)
def test_the_arbiter_choice_is_the_last_json_with_a_known_letter(raw: str, expected: tuple[str, str] | None) -> None:
    assert parse_arbiter_choice(raw) == expected


def test_a_class_that_needs_a_manager_never_continues_the_campaign() -> None:
    assert vote_action("refuses", False) is Gate.CONTINUE
    assert vote_action("refuses", None) is Gate.CONTINUE
    assert vote_action("refuses", True) is Gate.STOP
    assert vote_action("waiting_seller", False) is Gate.STOP


def test_the_manager_is_the_last_non_buyer_message_that_is_neither_a_bot_nor_an_auto_reply() -> None:
    assert last_reply_is_from_manager(MANAGER_CONTEXT)
    assert not last_reply_is_from_manager(BOT_CONTEXT)
    assert not last_reply_is_from_manager([(ROLE_SELLER, "Сообщение выше автоматическое и ответа не требует.")])
    assert last_reply_is_from_manager([(ROLE_SELLER, "Мы оформим возврат"), (ROLE_BUYER, "ок")])
    assert not last_reply_is_from_manager([])


def test_the_order_of_the_candidates_follows_the_key_and_is_not_one_sided() -> None:
    assert swap_candidates("abc") == swap_candidates("abc")
    assert {swap_candidates(str(uuid.UUID(int=number))) for number in range(40)} == {True, False}


# ---- движок ----


async def test_judges_that_agree_decide_without_the_arbiter() -> None:
    machine, llm = engine({QWEN: verdict("refuses"), GPT: verdict("refuses")})

    decision = await decide(machine)

    assert (decision.action, decision.category, decision.path) == (Gate.CONTINUE, "refuses", PATH_AGREE)
    assert llm.models() == [QWEN, GPT] and decision.needs_manager is False
    assert decision.model == f"{QWEN}+{GPT}" and (decision.tokens_in, decision.tokens_out) == (2000, 80)
    assert llm.calls[1][3:] == (400, {"reasoning_effort": "low"}) and llm.calls[0][3:] == (150, None)
    assert [vote.role for vote in decision.votes] == ["judge_a", "judge_b"]


async def test_agreeing_on_stop_with_different_classes_keeps_the_class_with_a_flag() -> None:
    machine, _ = engine({QWEN: verdict("question", True), GPT: verdict("threat", True)})

    decision = await decide(machine)

    assert (decision.action, decision.category, decision.flags, decision.path) == (
        Gate.STOP,
        "threat",
        ("attention",),
        PATH_AGREE,
    )


@pytest.mark.parametrize("seed", [str(uuid.UUID(int=number)) for number in range(6)])
async def test_the_arbiter_picks_one_of_the_two_verdicts_whatever_the_order(seed: str) -> None:
    machine, llm = engine(
        {QWEN: verdict("refuses"), GPT: verdict("waiting_seller", True), ARBITER: prefer("waiting_seller")}
    )

    decision = await decide(machine, seed=seed)

    assert (decision.action, decision.category, decision.path) == (Gate.STOP, "waiting_seller", PATH_ARBITER)
    assert llm.models() == [QWEN, GPT, ARBITER] and decision.votes[-1].choice == "judge_b"
    arbiter_prompt = llm.calls[-1][2]
    assert QWEN not in arbiter_prompt and GPT not in arbiter_prompt and llm.calls[-1][3] == 1500


async def test_the_arbiter_can_take_the_side_of_the_continuing_judge() -> None:
    machine, _ = engine({QWEN: verdict("refuses"), GPT: verdict("question", True), ARBITER: prefer("refuses")})

    decision = await decide(machine)

    assert (decision.action, decision.category, decision.votes[-1].choice) == (Gate.CONTINUE, "refuses", "judge_a")


async def test_neither_verdict_stops_the_campaign() -> None:
    machine, _ = engine({QWEN: verdict("refuses"), GPT: verdict("question", True), ARBITER: choice("neither")})

    decision = await decide(machine)

    assert (decision.action, decision.category, decision.path) == (Gate.STOP, "question", PATH_ARBITER)
    assert decision.rules_applied == (RULE_ARBITER_NEITHER,) and decision.needs_manager is True


async def test_a_failed_arbiter_hands_over_to_the_reserve() -> None:
    machine, llm = engine(
        {
            QWEN: verdict("refuses"),
            GPT: verdict("question", True),
            ARBITER: LLMTransientError("429", status_code=429),
            RESERVE: prefer("question"),
        }
    )

    decision = await decide(machine)

    assert (decision.action, decision.path) == (Gate.STOP, PATH_RESERVE)
    assert llm.models() == [QWEN, GPT, ARBITER, RESERVE] and decision.votes[-1].role == "reserve"


async def test_an_unreadable_arbiter_answer_counts_as_a_failure() -> None:
    machine, _ = engine(
        {QWEN: verdict("refuses"), GPT: verdict("question", True), ARBITER: "не знаю", RESERVE: prefer("refuses")}
    )

    decision = await decide(machine)

    assert (decision.path, decision.category) == (PATH_RESERVE, "refuses")


async def test_without_any_arbiter_the_dialog_waits_and_the_judges_stay_in_the_log() -> None:
    machine, _ = engine(
        {
            QWEN: verdict("refuses"),
            GPT: verdict("question", True),
            ARBITER: LLMError("таймаут"),
            RESERVE: LLMAnswerError("пусто"),
        }
    )

    decision = await decide(machine)

    assert (decision.action, decision.path, decision.category) == (Gate.WAIT, PATH_DEGRADED, None)
    assert "таймаут" in decision.reason and "пусто" in decision.reason
    assert [vote.role for vote in decision.votes] == ["judge_a", "judge_b"]


async def test_when_a_judge_fails_the_arbiter_classifies_the_slice_itself() -> None:
    machine, llm = engine(
        {
            QWEN: LLMTransientError("429", status_code=429),
            GPT: verdict("refuses"),
            ARBITER: verdict("waiting_seller", True),
        }
    )

    decision = await decide(machine)

    assert (decision.action, decision.category, decision.path) == (Gate.STOP, "waiting_seller", PATH_ARBITER)
    assert [vote.role for vote in decision.votes] == ["judge_b", "arbiter"]
    assert llm.calls[-1][1] == llm.calls[0][1]


async def test_the_prompts_can_be_replaced_for_an_experiment() -> None:
    llm = PanelLLM({QWEN: verdict("refuses"), GPT: verdict("question", True), ARBITER: prefer("refuses")})
    machine = DecisionEngine(llm, SPEC, system_prompt="судья", arbiter_prompt="арбитр")

    await decide(machine)

    assert {call[0]: call[1] for call in llm.calls} == {QWEN: "судья", GPT: "судья", ARBITER: "арбитр"}


async def test_a_noise_reply_to_a_live_manager_is_a_stop_that_needs_no_arbiter() -> None:
    machine, llm = engine({QWEN: verdict("noise"), GPT: verdict("noise")})

    decision = await decide(machine, context=MANAGER_CONTEXT)

    assert (decision.action, decision.category, decision.needs_manager) == (Gate.STOP, "waiting_seller", True)
    assert decision.rules_applied == (RULE_NOISE_AFTER_MANAGER,) and decision.path == PATH_AGREE
    assert len(llm.calls) == 2


async def test_a_noise_reply_to_the_bot_still_continues() -> None:
    machine, _ = engine({QWEN: verdict("noise"), GPT: verdict("noise")})

    decision = await decide(machine, context=BOT_CONTEXT)

    assert (decision.action, decision.category, decision.rules_applied) == (Gate.CONTINUE, "noise", ())


async def test_a_class_that_contradicts_the_flag_is_stored_as_a_stop_class() -> None:
    machine, _ = engine({QWEN: verdict("refuses", True), GPT: verdict("not_helped", True)})

    decision = await decide(machine)

    assert (decision.action, decision.category, decision.needs_manager) == (Gate.STOP, "waiting_seller", True)
    assert decision.rules_applied == (RULE_CONFLICT_STOP,)


async def test_a_guard_stops_without_asking_any_model() -> None:
    machine, llm = engine({})

    decision = await machine.decide(BOT_CONTEXT, ["номер"], seed="s", guard="attachment")

    assert (decision.action, decision.path, decision.votes) == (Gate.STOP, PATH_GUARD, ()) and llm.calls == []


def test_the_log_carries_the_votes_without_texts() -> None:
    vote = Vote("judge_a", QWEN, "refuses", False, "причина", 10, 5, 300)

    assert vote.log() == {
        "role": "judge_a",
        "model": QWEN,
        "category": "refuses",
        "needs_manager": False,
        "action": "continue",
        "tokens_in": 10,
        "tokens_out": 5,
        "latency_ms": 300,
    }


# ---- консьюмер ----


def panel_consumer(database: Database, llm: PanelLLM) -> ReplyConsumer:
    return ReplyConsumer(database, "unused:9092", "test-group", FakeLLM(), DecisionEngine(llm, SPEC))  # type: ignore[arg-type]


async def test_the_panel_decision_is_stored_with_the_path_the_votes_and_the_totals(
    database: Database, seller: uuid.UUID
) -> None:
    await feed(database, seller, [*BASE_FEED, REPLY], read_at=NOW)
    row_id = await request_reply(database, seller, REPLY)
    llm = PanelLLM({QWEN: verdict("refuses"), GPT: verdict("question", True), ARBITER: prefer("question")})

    await deliver(panel_consumer(database, llm), row_id, seller)

    row = await stored(database, row_id)
    assert (row.status, row.category, row.needs_manager, row.decision_path) == (
        VERDICT_DONE,
        "question",
        True,
        "arbiter",
    )
    assert row.model == f"{QWEN}+{GPT}+{ARBITER}" and (row.tokens_in, row.tokens_out) == (3000, 120)
    assert row.decision_votes is not None and row.decision_votes["policy_version"] == "d1"
    assert [vote["role"] for vote in row.decision_votes["votes"]] == ["judge_a", "judge_b", "arbiter"]
    assert "не буду я ничего удалять" not in json.dumps(row.decision_votes, ensure_ascii=False)
    assert "1. не буду я ничего удалять" in llm.calls[0][2]


async def test_without_a_decision_the_row_fails_for_the_retry_and_keeps_the_judge_votes(
    database: Database, seller: uuid.UUID
) -> None:
    await feed(database, seller, [*BASE_FEED, REPLY], read_at=NOW)
    row_id = await request_reply(database, seller, REPLY)
    llm = PanelLLM(
        {QWEN: verdict("refuses"), GPT: verdict("question", True), ARBITER: LLMError("429"), RESERVE: LLMError("500")}
    )

    await deliver(panel_consumer(database, llm), row_id, seller)

    row = await stored(database, row_id)
    assert row.status == VERDICT_FAILED and row.category is None and row.decision_path == "degraded"
    assert row.error is not None and "429" in row.error
    assert row.decision_votes is not None and len(row.decision_votes["votes"]) == 2


def test_the_engine_is_off_by_default() -> None:
    assert SupportBotConfig().reply_engine == "single"
    assert LLMConfig().model == SupportBotConfig().reply_judge_a_model


# ---- клиент ----


async def test_the_client_overrides_model_limit_and_extra_body_for_one_call() -> None:
    import httpx
    import respx

    url = "https://llm.test/v1/chat/completions"
    with respx.mock:
        route = respx.post(url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok", "reasoning_content": "длинные рассуждения"}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                },
            )
        )
        client = LLMClient(LLMConfig(base_url="https://llm.test/v1", model="base", api_key="k"))

        result = await client.complete("s", "u", model="other", max_tokens=900, extra_body={"reasoning_effort": "low"})
        plain = await client.complete("s", "u")

    first, second = (json.loads(call.request.read()) for call in route.calls)
    assert (first["model"], first["max_tokens"], first["reasoning_effort"]) == ("other", 900, "low")
    assert (second["model"], second["max_tokens"], "reasoning_effort" in second) == ("base", 150, False)
    assert (result.text, result.model, plain.model) == ("ok", "other", "base")


# ---- пометка «менеджер уже отвечал» ----


def test_the_user_message_says_whether_a_manager_has_replied_only_when_it_is_known() -> None:
    assert "Менеджер уже отвечал в этом диалоге: нет" in reply_user_message([], ["x"], False)
    assert "Менеджер уже отвечал в этом диалоге: да" in reply_user_message([], ["x"], True)
    assert "Менеджер уже отвечал" not in reply_user_message([], ["x"])


def test_only_a_live_seller_message_counts_as_a_manager_reply() -> None:
    first_at = PROMPT_AT + timedelta(days=2)
    reminders = {"Напоминание бота"}
    auto = event(1, "specialist", "Сообщение выше автоматическое и ответа не требует.")
    bot = event(2, "bot", "Напоминание бота")
    notice = event(3, "specialist", "Мы удалили это сообщение — оно нарушает правила Wildberries")
    buyer = event(4, "buyer", "не работает")
    human = event(5, "specialist", "Опишите проблему подробнее")
    later = event(60 * 72, "specialist", "Ответ уже после среза")

    assert manager_replied_before([event(0, "prompt", "Отзыв"), auto, bot, notice, buyer], first_at, reminders) is False
    assert manager_replied_before([auto, bot, buyer, human], first_at, reminders) is True
    assert manager_replied_before([auto, later], first_at, reminders) is False


def test_manager_message_times_skip_bot_auto_reply_and_notices() -> None:
    reminders = {"Напоминание бота"}
    auto = event(1, "specialist", "Сообщение выше автоматическое и ответа не требует.")
    bot = event(2, "bot", "Напоминание бота")
    buyer = event(4, "buyer", "не работает")
    human = event(5, "specialist", "Опишите проблему подробнее")
    later = event(60 * 72, "specialist", "Ответ позже")

    assert manager_message_times([auto, bot, buyer], reminders) == []
    assert manager_message_times([auto, bot, buyer, human, later], reminders) == [human.added_at, later.added_at]


async def test_the_note_about_the_manager_reaches_the_judges_and_the_arbiter() -> None:
    machine, llm = engine({QWEN: verdict("refuses"), GPT: verdict("question", True), ARBITER: prefer("refuses")})

    await machine.decide(BOT_CONTEXT, ["Второй"], seed="s", manager_replied=False)

    assert all("Менеджер уже отвечал в этом диалоге: нет" in call[2] for call in llm.calls)
    assert llm.models() == [QWEN, GPT, ARBITER]
