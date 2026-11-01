import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from backend.modules.wb_support_bot.domain.decision import (
    ACTION_SYSTEM_PROMPT,
    ARBITER_SYSTEM_PROMPT,
    CHOICE_A,
    CHOICE_NEITHER,
    PATH_AGREE,
    PATH_ARBITER,
    PATH_DEGRADED,
    PATH_GUARD,
    PATH_RESERVE,
    RULE_ARBITER_NEITHER,
    RULE_CONFLICT_STOP,
    VOTE_ARBITER,
    VOTE_JUDGE_A,
    VOTE_JUDGE_B,
    VOTE_RESERVE,
    Decision,
    Vote,
    apply_vote_rules,
    arbiter_user_message,
    flags_of,
    parse_arbiter_choice,
    parse_panel_verdict,
    settled_category,
    swap_candidates,
)
from backend.modules.wb_support_bot.domain.reply import (
    CATEGORY_FLAGS,
    REASON_LIMIT,
    REPLY_UNCLEAR,
    Gate,
    reply_user_message,
)
from backend.shared.llm import LLMAnswerError, LLMError, LLMResult


class Completer(Protocol):
    async def complete(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> LLMResult: ...


@dataclass(frozen=True, slots=True)
class RoleSpec:
    """Модель на роль: рассуждения включаются самой моделью, у судей их выключают `extra_body`."""

    model: str
    max_tokens: int
    extra_body: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PanelSpec:
    judge_a: RoleSpec
    judge_b: RoleSpec
    arbiter: RoleSpec
    reserve: RoleSpec
    policy_version: str


class DecisionEngine:
    """Слой принятия решения: два судьи, при расхождении арбитр, затем резервный арбитр.

    Рассылка видит только `Decision`. Если не ответил ни один арбитр, решения нет (`Gate.WAIT`):
    диалог ждёт, а предварительные вердикты судей остаются в журнале.
    """

    def __init__(
        self,
        llm: Completer,
        spec: PanelSpec,
        *,
        system_prompt: str = ACTION_SYSTEM_PROMPT,
        arbiter_prompt: str = ARBITER_SYSTEM_PROMPT,
    ) -> None:
        self.llm = llm
        self.spec = spec
        self.system = system_prompt
        self.arbiter_system = arbiter_prompt

    async def decide(
        self,
        context: list[tuple[str, str]],
        messages: list[str],
        *,
        seed: str,
        guard: str | None = None,
        manager_replied: bool | None = None,
    ) -> Decision:
        started = time.monotonic()
        version = self.spec.policy_version
        if guard is not None:
            return Decision(Gate.STOP, None, True, (), f"остановлено без модели: {guard}", PATH_GUARD, version)

        user = reply_user_message(context, messages, manager_replied)
        results = await asyncio.gather(
            self._classify(VOTE_JUDGE_A, self.spec.judge_a, user),
            self._classify(VOTE_JUDGE_B, self.spec.judge_b, user),
        )
        votes = [apply_vote_rules(vote, context) for vote in results if vote is not None]
        failures = [role for role, vote in zip((VOTE_JUDGE_A, VOTE_JUDGE_B), results, strict=True) if vote is None]

        if len(votes) == 2 and votes[0].action is votes[1].action:
            return self._decision(PATH_AGREE, votes, _preferred(votes), started)

        for role, spec, path in (
            (VOTE_ARBITER, self.spec.arbiter, PATH_ARBITER),
            (VOTE_RESERVE, self.spec.reserve, PATH_RESERVE),
        ):
            try:
                if len(votes) == 2:
                    verdict = await self._arbitrate(role, spec, context, messages, votes, seed, manager_replied)
                else:
                    verdict = apply_vote_rules(await self._classify_or_raise(role, spec, user), context)
            except LLMError as error:
                failures.append(f"{role}: {error}"[:REASON_LIMIT])
                continue
            return self._decision(path, [*votes, verdict], verdict, started)

        reason = "; ".join(failures) or "арбитр не ответил"
        return Decision(
            Gate.WAIT,
            None,
            True,
            (),
            reason[:REASON_LIMIT],
            PATH_DEGRADED,
            version,
            (),
            _elapsed(started),
            tuple(votes),
        )

    async def _classify(self, role: str, spec: RoleSpec, user: str) -> Vote | None:
        try:
            return await self._classify_or_raise(role, spec, user)
        except LLMError:
            return None

    async def _classify_or_raise(self, role: str, spec: RoleSpec, user: str) -> Vote:
        result = await self.llm.complete(
            self.system, user, model=spec.model, max_tokens=spec.max_tokens, extra_body=spec.extra_body or None
        )
        category, needs_manager, reason = parse_panel_verdict(result.text)
        return Vote(
            role, result.model, category, needs_manager, reason, result.tokens_in, result.tokens_out, result.latency_ms
        )

    async def _arbitrate(
        self,
        role: str,
        spec: RoleSpec,
        context: list[tuple[str, str]],
        messages: list[str],
        votes: list[Vote],
        seed: str,
        manager_replied: bool | None = None,
    ) -> Vote:
        swapped = swap_candidates(seed)
        first, second = (votes[1], votes[0]) if swapped else (votes[0], votes[1])
        result = await self.llm.complete(
            self.arbiter_system,
            arbiter_user_message(context, messages, first, second, manager_replied),
            model=spec.model,
            max_tokens=spec.max_tokens,
            extra_body=spec.extra_body or None,
        )
        picked = parse_arbiter_choice(result.text)
        if picked is None:
            raise LLMAnswerError("арбитр не выбрал вердикт")
        choice, reason = picked
        tokens = (result.tokens_in, result.tokens_out, result.latency_ms)
        if choice == CHOICE_NEITHER:
            category = next((vote.category for vote in votes if vote.action is Gate.STOP), REPLY_UNCLEAR)
            return Vote(
                role, result.model, category, True, reason or "ни один вердикт не подошёл", *tokens,
                CHOICE_NEITHER, (RULE_ARBITER_NEITHER,),
            )  # fmt: skip
        chosen = first if choice == CHOICE_A else second
        return Vote(
            role, result.model, chosen.category, chosen.needs_manager, reason or chosen.reason, *tokens,
            chosen.role, chosen.rules,
        )  # fmt: skip

    def _decision(self, path: str, votes: list[Vote], chosen: Vote, started: float) -> Decision:
        category, replaced = settled_category(chosen.action, chosen.category)
        rules = list(dict.fromkeys(rule for vote in votes for rule in vote.rules))
        if replaced and RULE_CONFLICT_STOP not in rules:
            rules.append(RULE_CONFLICT_STOP)
        return Decision(
            chosen.action,
            category,
            bool(chosen.needs_manager) or replaced,
            flags_of(category),
            chosen.reason,
            path,
            self.spec.policy_version,
            tuple(rules),
            _elapsed(started),
            tuple(votes),
        )


def _preferred(votes: list[Vote]) -> Vote:
    """Согласные по действию судьи могут назвать разные классы: класс с флагом (угроза, «не писать») важнее."""
    return next((vote for vote in votes if vote.category in CATEGORY_FLAGS), votes[0])


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


__all__ = ["Completer", "DecisionEngine", "PanelSpec", "RoleSpec"]
