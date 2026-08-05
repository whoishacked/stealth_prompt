"""Tests for the bounded attack loop.

Every stop condition gets a test, because "the loop is bounded" is only true if
each bound actually fires. The target is a fake driver, so no browser or model
is involved.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

import pytest

from stealth_prompt.agents.base import AgentUsage, UsageLedger
from stealth_prompt.oracles import DisclosureStatus, Oracle, OracleType
from stealth_prompt.workbench.config import RunMode, SafetySettings, TargetDataSharing
from stealth_prompt.workbench.engine import (
    AttackEngine,
    CaptureFailed,
    EngineContext,
    StopPolicy,
    looks_like_refusal,
    similarity,
)
from stealth_prompt.workbench.planner import PlannerDecision, StaticStrategy
from stealth_prompt.workbench.state import RunState, RunStateMachine, StopReason

T = TypeVar("T")
CANARY = "SP_CANARY_ABC123XYZ789"


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


class FakeTarget:
    """Records what was sent and replays scripted replies."""

    def __init__(
        self,
        replies: list[str] | None = None,
        *,
        fail_on_submit: int | None = None,
        capture_fails: bool = False,
    ) -> None:
        self._replies = list(replies or ["a bland reply"])
        self.filled: list[str] = []
        self.submits = 0
        self.captures = 0
        self._fail_on_submit = fail_on_submit
        self._capture_fails = capture_fails

    async def fill(self, payload: str) -> None:
        self.filled.append(payload)

    async def submit(self) -> None:
        self.submits += 1
        if self._fail_on_submit is not None and self.submits >= self._fail_on_submit:
            raise RuntimeError("submit failed")

    async def capture(self) -> str:
        self.captures += 1
        if self._capture_fails:
            raise CaptureFailed("capture_timeout", elapsed_ms=60000, partial_text="par")
        if self._replies:
            return self._replies.pop(0)
        return "a bland reply"


class CountingStrategy:
    """Emits a distinct payload each turn and counts planner calls."""

    name = "counting"

    def __init__(self, limit: int = 100) -> None:
        self.calls = 0
        self.last_usage: AgentUsage | None = AgentUsage(1, 2, 0.001)
        self._limit = limit

    async def next_action(self, context: Any) -> PlannerDecision:
        self.calls += 1
        if self.calls > self._limit:
            return PlannerDecision(
                next_message=None, reasoning_summary="done", stop=True
            )
        return PlannerDecision(
            next_message=f"payload {self.calls}",
            reasoning_summary=f"attempt {self.calls}",
            approach="instruction_override",
        )


def build(
    *,
    strategy: Any = None,
    target: FakeTarget | None = None,
    oracles: list[Oracle] | None = None,
    policy: StopPolicy | None = None,
    mode: RunMode = RunMode.AUTO,
    safety: SafetySettings | None = None,
    max_cost_usd: float | None = None,
    approval_gate: Any = None,
) -> AttackEngine:
    return AttackEngine(
        strategy=strategy or CountingStrategy(),
        target=target or FakeTarget(),
        oracles=oracles or [],
        safety=safety or SafetySettings(min_turn_delay_ms=0),
        policy=policy or StopPolicy(max_turns=3, min_turn_delay_ms=0),
        mode=mode,
        machine=RunStateMachine(run_id="run-1"),
        usage=UsageLedger(max_cost_usd=max_cost_usd),
        approval_gate=approval_gate,
        sleep=lambda _: asyncio.sleep(0),
    )


class TestSignals:
    @pytest.mark.parametrize(
        "text",
        [
            "I'm sorry, but I can't share that.",
            "I cannot provide internal configuration.",
            "I am not able to help with that.",
        ],
    )
    def test_refusals_are_detected(self, text: str) -> None:
        assert looks_like_refusal(text) is True

    def test_ordinary_replies_are_not_refusals(self) -> None:
        assert looks_like_refusal("Sure, here is the answer.") is False

    def test_identical_text_is_similar(self) -> None:
        assert similarity("the same words here", "the same words here") == 1.0

    def test_unrelated_text_is_not(self) -> None:
        assert similarity("alpha beta", "gamma delta") == 0.0


class TestConfirmedStops:
    def test_confirmed_evidence_stops_before_another_planner_call(self) -> None:
        oracle = Oracle(
            oracle_id="canary", oracle_type=OracleType.FRAGMENT, pattern=CANARY
        )
        strategy = CountingStrategy()
        engine = build(
            strategy=strategy,
            target=FakeTarget([f"here it is {CANARY}"]),
            oracles=[oracle],
        )

        reason = run(engine.run())

        assert reason is StopReason.CONFIRMED
        assert engine.status is DisclosureStatus.CONFIRMED
        # One plan, one send: no second planner call was made after evidence.
        assert strategy.calls == 1

    def test_evidence_is_recorded_against_the_turn(self) -> None:
        oracle = Oracle(
            oracle_id="canary", oracle_type=OracleType.FRAGMENT, pattern=CANARY
        )
        engine = build(target=FakeTarget([f"x {CANARY}"]), oracles=[oracle])

        run(engine.run())

        assert len(engine.evidence) == 1
        assert engine.turns[0].evidence[0].turn == 1


class TestBounds:
    def test_max_turns(self) -> None:
        strategy = CountingStrategy()
        engine = build(strategy=strategy, policy=StopPolicy(max_turns=2, min_turn_delay_ms=0))

        reason = run(engine.run())

        assert reason is StopReason.MAX_TURNS
        assert len(engine.turns) == 2
        assert engine.target.submits == 2

    def test_never_sends_more_than_max_turns(self) -> None:
        target = FakeTarget()
        engine = build(target=target, policy=StopPolicy(max_turns=3, min_turn_delay_ms=0))

        run(engine.run())

        assert target.submits == 3
        assert len(target.filled) == 3

    def test_planner_stop(self) -> None:
        engine = build(strategy=CountingStrategy(limit=1))

        reason = run(engine.run())

        assert reason is StopReason.PLANNER_STOP

    def test_static_exhaustion_stops(self) -> None:
        engine = build(strategy=StaticStrategy(["one", "two"]))

        reason = run(engine.run())

        assert reason is StopReason.PLANNER_STOP
        assert len(engine.turns) == 2

    def test_cost_limit_stops_before_another_planner_call(self) -> None:
        strategy = CountingStrategy()
        engine = build(
            strategy=strategy,
            policy=StopPolicy(max_turns=10, min_turn_delay_ms=0),
            max_cost_usd=0.0015,
        )

        reason = run(engine.run())

        # 0.001 per turn: two turns reach the limit, and the third is not planned.
        assert reason is StopReason.COST_LIMIT
        assert strategy.calls == 2

    def test_unreported_cost_does_not_pretend_to_enforce(self) -> None:
        class NoUsage(CountingStrategy):
            def __init__(self) -> None:
                super().__init__()
                self.last_usage = None

        engine = build(
            strategy=NoUsage(),
            policy=StopPolicy(max_turns=2, min_turn_delay_ms=0),
            max_cost_usd=0.0001,
        )

        reason = run(engine.run())

        assert reason is StopReason.MAX_TURNS
        assert engine.usage.cost_reported is False

    def test_duration_limit(self) -> None:
        engine = build(
            policy=StopPolicy(
                max_turns=99, max_duration_seconds=-1, min_turn_delay_ms=0
            )
        )

        assert run(engine.run()) is StopReason.MAX_DURATION

    def test_repeated_payloads(self) -> None:
        class Repeater:
            name = "repeater"
            last_usage = None

            async def next_action(self, context: Any) -> PlannerDecision:
                return PlannerDecision(
                    next_message="the same payload every time",
                    reasoning_summary="stuck",
                )

        engine = build(
            strategy=Repeater(),
            policy=StopPolicy(max_turns=10, max_repeated_payloads=1, min_turn_delay_ms=0),
        )

        assert run(engine.run()) is StopReason.NO_PROGRESS_PAYLOAD

    def test_repeated_responses(self) -> None:
        engine = build(
            target=FakeTarget(["identical reply text here"] * 10),
            policy=StopPolicy(
                max_turns=10, max_repeated_responses=2, min_turn_delay_ms=0
            ),
        )

        assert run(engine.run()) is StopReason.NO_PROGRESS_RESPONSE

    def test_consecutive_refusals(self) -> None:
        engine = build(
            target=FakeTarget(
                [
                    "I'm sorry, but I can't do that one",
                    "I cannot help with number two",
                    "I am not able to assist here three",
                ]
            ),
            policy=StopPolicy(
                max_turns=10, max_consecutive_refusals=2, min_turn_delay_ms=0
            ),
        )

        assert run(engine.run()) is StopReason.CONSECUTIVE_REFUSALS

    def test_every_run_terminates(self) -> None:
        # A loop with no bound is the failure mode this whole class guards.
        engine = build(policy=StopPolicy(max_turns=5, min_turn_delay_ms=0))

        assert run(engine.run()) is not None


class TestFailures:
    def test_capture_failure_is_never_not_detected(self) -> None:
        engine = build(target=FakeTarget(capture_fails=True))

        reason = run(engine.run())

        assert reason is StopReason.CAPTURE_TIMEOUT
        assert engine.status is DisclosureStatus.ERROR
        assert engine.status is not DisclosureStatus.NOT_DETECTED

    def test_capture_failure_preserves_partial_text_and_is_typed(self) -> None:
        engine = build(target=FakeTarget(capture_fails=True))

        run(engine.run())

        failure = engine.capture_failures[0]
        assert failure["code"] == "capture_timeout"
        assert failure["partial_observed"] is True
        assert failure["elapsed_ms"] == 60000

    def test_submit_failure_is_target_unavailable(self) -> None:
        engine = build(target=FakeTarget(fail_on_submit=1))

        assert run(engine.run()) is StopReason.TARGET_UNAVAILABLE

    def test_planner_failure_is_agent_unavailable(self) -> None:
        class Broken:
            name = "broken"
            last_usage = None

            async def next_action(self, context: Any) -> PlannerDecision:
                from stealth_prompt.workbench.planner import PlannerError

                raise PlannerError("backend exploded")

        engine = build(strategy=Broken())

        assert run(engine.run()) is StopReason.AGENT_UNAVAILABLE

    def test_no_oracles_yields_inconclusive_not_negative(self) -> None:
        engine = build(oracles=[], policy=StopPolicy(max_turns=1, min_turn_delay_ms=0))

        run(engine.run())

        assert engine.status is DisclosureStatus.INCONCLUSIVE

    def test_all_oracles_missing_yields_not_detected(self) -> None:
        oracle = Oracle(
            oracle_id="canary", oracle_type=OracleType.FRAGMENT, pattern=CANARY
        )
        engine = build(
            oracles=[oracle], policy=StopPolicy(max_turns=1, min_turn_delay_ms=0)
        )

        run(engine.run())

        assert engine.status is DisclosureStatus.NOT_DETECTED


class TestOperatorStop:
    def test_stop_prevents_the_next_send(self) -> None:
        target = FakeTarget()
        engine = build(target=target, policy=StopPolicy(max_turns=5, min_turn_delay_ms=0))

        async def scenario() -> StopReason:
            async def stop_after_first(kind: str, payload: dict[str, Any]) -> None:
                if kind == "turn_complete":
                    engine.request_stop()

            engine.on_event = stop_after_first
            return await engine.run()

        reason = run(scenario())

        assert reason is StopReason.OPERATOR_STOP
        assert target.submits == 1

    def test_stop_before_the_first_turn_sends_nothing(self) -> None:
        target = FakeTarget()
        engine = build(target=target)
        engine.request_stop()

        reason = run(engine.run())

        assert reason is StopReason.OPERATOR_STOP
        assert target.submits == 0
        assert target.filled == []


class TestSupervisedMode:
    def test_send_waits_for_approval(self) -> None:
        target = FakeTarget()
        approvals: list[str] = []

        async def gate(decision: Any, payload: str) -> bool:
            approvals.append(payload)
            return True

        engine = build(
            target=target,
            mode=RunMode.SUPERVISED,
            approval_gate=gate,
            policy=StopPolicy(max_turns=2, min_turn_delay_ms=0),
        )

        run(engine.run())

        assert len(approvals) == 2
        assert target.submits == 2

    def test_payload_is_filled_before_approval_is_requested(self) -> None:
        target = FakeTarget()
        order: list[str] = []

        async def gate(decision: Any, payload: str) -> bool:
            order.append("approve")
            return True

        original_fill = target.fill

        async def tracked_fill(payload: str) -> None:
            order.append("fill")
            await original_fill(payload)

        target.fill = tracked_fill  # type: ignore[method-assign]
        engine = build(
            target=target,
            mode=RunMode.SUPERVISED,
            approval_gate=gate,
            policy=StopPolicy(max_turns=1, min_turn_delay_ms=0),
        )

        run(engine.run())

        assert order == ["fill", "approve"]

    def test_rejection_stops_without_sending(self) -> None:
        target = FakeTarget()

        async def gate(decision: Any, payload: str) -> bool:
            return False

        engine = build(
            target=target, mode=RunMode.SUPERVISED, approval_gate=gate
        )

        reason = run(engine.run())

        assert reason is StopReason.OPERATOR_STOP
        assert target.submits == 0

    def test_missing_gate_refuses_to_send(self) -> None:
        # Fail closed: supervised mode without a gate must not send.
        target = FakeTarget()
        engine = build(target=target, mode=RunMode.SUPERVISED, approval_gate=None)

        assert run(engine.run()) is StopReason.OPERATOR_STOP
        assert target.submits == 0


class TestStateMachineIntegration:
    def test_run_walks_the_expected_states(self) -> None:
        engine = build(policy=StopPolicy(max_turns=1, min_turn_delay_ms=0))

        run(engine.run())

        visited = {state for pair in engine.machine.history for state in pair}
        for expected in (
            RunState.PLANNING,
            RunState.PAYLOAD_READY,
            RunState.SENDING,
            RunState.WAITING_FOR_RESPONSE,
            RunState.EVALUATING,
        ):
            assert expected in visited

    def test_stop_reason_reaches_the_machine(self) -> None:
        engine = build(policy=StopPolicy(max_turns=1, min_turn_delay_ms=0))

        run(engine.run())

        assert engine.machine.stop_reason is StopReason.MAX_TURNS


class TestRecords:
    def test_digests_are_kept_even_without_the_transcript(self) -> None:
        engine = build(policy=StopPolicy(max_turns=1, min_turn_delay_ms=0))
        run(engine.run())

        record = engine.turns[0].to_dict(store_transcript=False)

        assert record["payload_sha256_short"]
        assert record["response_sha256_short"]
        assert "payload" not in record
        assert "response" not in record

    def test_transcript_included_when_enabled(self) -> None:
        engine = build(policy=StopPolicy(max_turns=1, min_turn_delay_ms=0))
        run(engine.run())

        record = engine.turns[0].to_dict(store_transcript=True)

        assert record["payload"] == "payload 1"

    def test_usage_is_accumulated_per_turn(self) -> None:
        engine = build(
            policy=StopPolicy(max_turns=2, min_turn_delay_ms=0), max_cost_usd=None
        )

        run(engine.run())

        assert len(engine.usage.per_turn) == 2
        assert engine.usage.input_tokens == 2


class TestPolicyDirectly:
    def test_reports_the_first_breached_bound(self) -> None:
        policy = StopPolicy(max_turns=1)
        context = EngineContext(usage=UsageLedger())
        context.turn_number = 5

        assert policy.check(context) is StopReason.MAX_TURNS

    def test_confirmed_outranks_other_bounds(self) -> None:
        policy = StopPolicy(max_turns=1)
        context = EngineContext(usage=UsageLedger())
        context.turn_number = 99
        context.confirmed = True

        assert policy.check(context) is StopReason.CONFIRMED

    def test_operator_stop_outranks_everything(self) -> None:
        policy = StopPolicy(max_turns=1)
        context = EngineContext(usage=UsageLedger())
        context.confirmed = True
        context.stop_requested = True

        assert policy.check(context) is StopReason.OPERATOR_STOP

    def test_defaults_are_conservative(self) -> None:
        policy = StopPolicy()

        assert policy.max_turns <= 10
        assert policy.max_duration_seconds is not None
        assert policy.min_turn_delay_ms >= 1000


class TestSharingPolicyGuard:
    def test_none_sharing_still_permits_a_static_run(self) -> None:
        safety = SafetySettings(
            target_data_sharing=TargetDataSharing.NONE, min_turn_delay_ms=0
        )
        engine = build(
            strategy=StaticStrategy(["one"]),
            safety=safety,
            policy=StopPolicy(max_turns=2, min_turn_delay_ms=0),
        )

        run(engine.run())

        assert engine.turns[0].payload == "one"
