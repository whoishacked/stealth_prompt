"""Behavioral tests for the deterministic fake agent backend.

These double as the acceptance criteria for the Claude and Codex adapters: the
same lifecycle, truncation, interrupt, timeout, and shutdown assertions are
re-run against those backends in phases 3 and 4 with recorded fixtures.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

import pytest

from stealth_prompt.agents import (
    AgentEvent,
    AgentEventKind,
    AgentRequest,
    AgentStateError,
    AgentUsage,
    FakeAgentAdapter,
)

T = TypeVar("T")


def run(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine to completion without a pytest async plugin."""
    return asyncio.run(coro)


async def collect(adapter: FakeAgentAdapter, request: AgentRequest) -> list[AgentEvent]:
    return [event async for event in adapter.send(request)]


def kinds(events: list[AgentEvent]) -> list[AgentEventKind]:
    return [event.kind for event in events]


def completed_text(events: list[AgentEvent]) -> str:
    for event in events:
        if event.kind is AgentEventKind.MESSAGE_COMPLETED:
            return event.text
    raise AssertionError("stream contained no MESSAGE_COMPLETED event")


class TestPreflight:
    def test_available_backend_reports_a_version(self) -> None:
        result = run(FakeAgentAdapter().preflight())

        assert result.available is True
        assert result.version is not None
        assert result.adapter_name == "fake"

    def test_unavailable_backend_reports_a_remedy(self) -> None:
        result = run(FakeAgentAdapter(available=False).preflight())

        assert result.available is False
        assert result.remedy


class TestStreaming:
    def test_streams_deltas_then_completes(self) -> None:
        adapter = FakeAgentAdapter([["alpha ", "beta ", "gamma"]])

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(adapter, AgentRequest(prompt="author a payload"))

        events = run(scenario())

        assert kinds(events) == [
            AgentEventKind.SESSION_STARTED,
            AgentEventKind.TEXT_DELTA,
            AgentEventKind.TEXT_DELTA,
            AgentEventKind.TEXT_DELTA,
            AgentEventKind.MESSAGE_COMPLETED,
        ]
        assert completed_text(events) == "alpha beta gamma"

    def test_session_is_announced_only_once(self) -> None:
        adapter = FakeAgentAdapter([["one"], ["two"]])

        async def scenario() -> tuple[list[AgentEvent], list[AgentEvent]]:
            await adapter.start()
            first = await collect(adapter, AgentRequest(prompt="p", turn=1))
            second = await collect(adapter, AgentRequest(prompt="p", turn=2))
            return first, second

        first, second = run(scenario())

        assert AgentEventKind.SESSION_STARTED in kinds(first)
        assert AgentEventKind.SESSION_STARTED not in kinds(second)

    def test_script_advances_with_the_turn_number(self) -> None:
        adapter = FakeAgentAdapter([["first"], ["second"]])

        async def scenario() -> tuple[str, str]:
            await adapter.start()
            one = completed_text(await collect(adapter, AgentRequest(prompt="p", turn=1)))
            two = completed_text(await collect(adapter, AgentRequest(prompt="p", turn=2)))
            return one, two

        assert run(scenario()) == ("first", "second")

    def test_turns_past_the_script_reuse_the_last_entry(self) -> None:
        adapter = FakeAgentAdapter([["only"]])

        async def scenario() -> str:
            await adapter.start()
            return completed_text(await collect(adapter, AgentRequest(prompt="p", turn=7)))

        assert run(scenario()) == "only"

    def test_prompts_are_recorded_in_order(self) -> None:
        adapter = FakeAgentAdapter()

        async def scenario() -> None:
            await adapter.start()
            await collect(adapter, AgentRequest(prompt="first prompt", turn=1))
            await collect(adapter, AgentRequest(prompt="second prompt", turn=2))

        run(scenario())

        assert adapter.prompts == ["first prompt", "second prompt"]

    def test_default_script_is_inert_english_text(self) -> None:
        adapter = FakeAgentAdapter()

        async def scenario() -> str:
            await adapter.start()
            return completed_text(await collect(adapter, AgentRequest(prompt="p")))

        text = run(scenario())

        assert "SP_CANARY" in text
        # A payload is prose for a human to review, never runnable content.
        for marker in ("<script", "javascript:", "os.system", "subprocess", "eval("):
            assert marker not in text

    def test_usage_is_reported_when_configured(self) -> None:
        usage = AgentUsage(input_tokens=12, output_tokens=34, cost_usd=0.001)
        adapter = FakeAgentAdapter(usage=usage)

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(adapter, AgentRequest(prompt="p"))

        events = run(scenario())

        assert kinds(events)[-1] is AgentEventKind.USAGE
        assert events[-1].usage == usage

    def test_no_usage_event_when_the_backend_reports_none(self) -> None:
        adapter = FakeAgentAdapter()

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(adapter, AgentRequest(prompt="p"))

        assert AgentEventKind.USAGE not in kinds(run(scenario()))


class TestOutputCap:
    def test_output_is_truncated_at_the_cap(self) -> None:
        adapter = FakeAgentAdapter([["0123456789", "never delivered"]])

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(
                adapter, AgentRequest(prompt="p", max_output_bytes=4)
            )

        events = run(scenario())
        completion = events[-1]

        assert completion.kind is AgentEventKind.MESSAGE_COMPLETED
        assert completion.text == "0123"
        assert completion.truncated is True

    def test_untruncated_completion_says_so(self) -> None:
        adapter = FakeAgentAdapter([["short"]])

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(adapter, AgentRequest(prompt="p", max_output_bytes=64))

        assert run(scenario())[-1].truncated is False


class TestInterrupt:
    def test_interrupt_stops_the_stream_mid_turn(self) -> None:
        adapter = FakeAgentAdapter([["one ", "two ", "three ", "four"]])

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            seen: list[AgentEvent] = []
            async for event in adapter.send(AgentRequest(prompt="p")):
                seen.append(event)
                if event.kind is AgentEventKind.TEXT_DELTA and len(seen) == 3:
                    await adapter.interrupt()
            return seen

        events = run(scenario())

        assert kinds(events)[-1] is AgentEventKind.INTERRUPTED
        assert AgentEventKind.MESSAGE_COMPLETED not in kinds(events)
        assert adapter.interrupt_count == 1

    def test_interrupted_event_carries_the_partial_text(self) -> None:
        adapter = FakeAgentAdapter([["alpha ", "beta ", "gamma"]])

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            seen: list[AgentEvent] = []
            async for event in adapter.send(AgentRequest(prompt="p")):
                seen.append(event)
                if event.kind is AgentEventKind.TEXT_DELTA:
                    await adapter.interrupt()
            return seen

        events = run(scenario())

        assert events[-1].kind is AgentEventKind.INTERRUPTED
        assert events[-1].text == "alpha "

    def test_interrupt_without_an_active_turn_is_safe(self) -> None:
        adapter = FakeAgentAdapter()

        run(adapter.interrupt())

        assert adapter.interrupt_count == 1

    def test_a_later_turn_runs_after_an_interrupt(self) -> None:
        adapter = FakeAgentAdapter([["a ", "b"], ["second turn"]])

        async def scenario() -> str:
            await adapter.start()
            async for event in adapter.send(AgentRequest(prompt="p", turn=1)):
                if event.kind is AgentEventKind.TEXT_DELTA:
                    await adapter.interrupt()
            return completed_text(await collect(adapter, AgentRequest(prompt="p", turn=2)))

        assert run(scenario()) == "second turn"


class TestTimeout:
    def test_a_stalled_turn_ends_with_a_timeout_error(self) -> None:
        adapter = FakeAgentAdapter(stall=True)

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(adapter, AgentRequest(prompt="p", timeout_ms=10))

        events = run(scenario())

        assert events[-1].kind is AgentEventKind.ERROR
        error = events[-1].error
        assert error is not None
        assert error.code == "agent_timeout"
        assert error.retryable is True

    def test_timeout_message_names_the_budget_and_leaks_nothing(self) -> None:
        adapter = FakeAgentAdapter(stall=True)

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(
                adapter, AgentRequest(prompt="secret prompt text", timeout_ms=10)
            )

        error = run(scenario())[-1].error
        assert error is not None
        assert "10 ms" in error.message
        assert "secret prompt text" not in error.message


class TestLifecycle:
    def test_send_before_start_is_rejected(self) -> None:
        adapter = FakeAgentAdapter()

        async def scenario() -> None:
            await collect(adapter, AgentRequest(prompt="p"))

        with pytest.raises(AgentStateError, match="call start\\(\\) before send\\(\\)"):
            run(scenario())

    def test_send_after_close_is_rejected(self) -> None:
        adapter = FakeAgentAdapter()

        async def scenario() -> None:
            await adapter.start()
            await adapter.close()
            await collect(adapter, AgentRequest(prompt="p"))

        with pytest.raises(AgentStateError, match="closed agent session"):
            run(scenario())

    def test_start_after_close_is_rejected(self) -> None:
        adapter = FakeAgentAdapter()

        async def scenario() -> None:
            await adapter.start()
            await adapter.close()
            await adapter.start()

        with pytest.raises(AgentStateError, match="closed agent session"):
            run(scenario())

    def test_start_is_idempotent(self) -> None:
        adapter = FakeAgentAdapter()

        async def scenario() -> None:
            await adapter.start()
            await adapter.start()

        run(scenario())

        assert adapter.start_count == 1

    def test_close_is_idempotent(self) -> None:
        adapter = FakeAgentAdapter()

        async def scenario() -> None:
            await adapter.start()
            await adapter.close()
            await adapter.close()

        run(scenario())

        assert adapter.close_count == 1
        assert adapter.closed is True

    def test_close_without_start_is_safe(self) -> None:
        adapter = FakeAgentAdapter()

        run(adapter.close())

        assert adapter.closed is True
