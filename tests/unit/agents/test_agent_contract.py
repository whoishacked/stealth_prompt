"""Contract tests for the agent protocol.

The most important test here is
:meth:`TestNoExecutionChannel.test_event_fields_are_inert`. The workbench's
central safety property is that an agent can propose payload *text* and nothing
else -- it cannot name a browser action, a shell command, or a script. That
property is enforced by the shape of the event union rather than by filtering
model output, so it is asserted structurally.
"""

from __future__ import annotations

import dataclasses

import pytest

from stealth_prompt.agents import (
    MAX_OUTPUT_BYTES,
    MAX_PROMPT_BYTES,
    AgentAdapter,
    AgentErrorInfo,
    AgentEvent,
    AgentEventKind,
    AgentKind,
    AgentLimits,
    AgentRequest,
    AgentTimeoutError,
    AgentUsage,
    FakeAgentAdapter,
    TurnAccumulator,
    build_agent_adapter,
    implemented_agent_kinds,
)

# Field or member names that would represent an execution channel.
EXECUTION_WORDS = {
    "action",
    "argv",
    "code",
    "command",
    "eval",
    "exec",
    "javascript",
    "js",
    "locator",
    "operation",
    "script",
    "selector",
    "shell",
    "tool",
    "tool_call",
    "tool_use",
}


class TestNoExecutionChannel:
    def test_event_fields_are_inert(self) -> None:
        names = {f.name for f in dataclasses.fields(AgentEvent)}

        assert names == {
            "kind",
            "text",
            "session_id",
            "usage",
            "error",
            "truncated",
            "sequence",
        }
        assert not (names & EXECUTION_WORDS)

    def test_event_kinds_are_inert(self) -> None:
        members = {kind.value for kind in AgentEventKind}

        assert not (members & EXECUTION_WORDS)
        assert members == {
            "session_started",
            "text_delta",
            "message_completed",
            "usage",
            "interrupted",
            "error",
        }

    def test_request_fields_are_inert(self) -> None:
        names = {f.name for f in dataclasses.fields(AgentRequest)}

        assert not (names & EXECUTION_WORDS)

    def test_adapter_protocol_exposes_no_execution_method(self) -> None:
        methods = {name for name in dir(AgentAdapter) if not name.startswith("_")}

        assert not (methods & EXECUTION_WORDS)


class TestAgentEvent:
    def test_error_event_requires_error_information(self) -> None:
        with pytest.raises(ValueError, match="must carry error information"):
            AgentEvent(kind=AgentEventKind.ERROR)

    def test_error_event_with_information_is_accepted(self) -> None:
        info = AgentErrorInfo(code="agent_timeout", message="timed out")

        event = AgentEvent(kind=AgentEventKind.ERROR, error=info)

        assert event.error is info

    def test_negative_sequence_rejected(self) -> None:
        with pytest.raises(ValueError, match="sequence cannot be negative"):
            AgentEvent(kind=AgentEventKind.TEXT_DELTA, sequence=-1)

    def test_events_are_frozen(self) -> None:
        event = AgentEvent(kind=AgentEventKind.TEXT_DELTA, text="hi")

        with pytest.raises(dataclasses.FrozenInstanceError):
            event.text = "changed"


class TestAgentRequest:
    @pytest.mark.parametrize("prompt", ["", "   ", "\n\t "])
    def test_blank_prompt_rejected(self, prompt: str) -> None:
        with pytest.raises(ValueError, match="prompt cannot be empty"):
            AgentRequest(prompt=prompt)

    def test_oversized_prompt_rejected(self) -> None:
        with pytest.raises(ValueError, match="above the"):
            AgentRequest(prompt="a" * (MAX_PROMPT_BYTES + 1))

    def test_prompt_size_is_measured_in_bytes_not_characters(self) -> None:
        # Four-byte characters must count as four, or a multibyte prompt could
        # slip past a limit that exists to bound memory.
        emoji = "\U0001f600"
        assert len(emoji.encode("utf-8")) == 4

        with pytest.raises(ValueError, match="above the"):
            AgentRequest(prompt=emoji * (MAX_PROMPT_BYTES // 4 + 1))

    def test_turn_numbering_starts_at_one(self) -> None:
        with pytest.raises(ValueError, match="turn numbering starts at 1"):
            AgentRequest(prompt="hello", turn=0)

    @pytest.mark.parametrize("timeout", [0, -1])
    def test_non_positive_timeout_rejected(self, timeout: int) -> None:
        with pytest.raises(ValueError, match="timeout_ms must be positive"):
            AgentRequest(prompt="hello", timeout_ms=timeout)

    @pytest.mark.parametrize("size", [0, -1, MAX_OUTPUT_BYTES + 1])
    def test_output_cap_bounds_enforced(self, size: int) -> None:
        with pytest.raises(ValueError, match="max_output_bytes"):
            AgentRequest(prompt="hello", max_output_bytes=size)


class TestAgentUsage:
    def test_negative_tokens_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            AgentUsage(input_tokens=-1)

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValueError, match="cost cannot be negative"):
            AgentUsage(cost_usd=-0.01)


class TestAgentLimits:
    def test_defaults_are_bounded(self) -> None:
        limits = AgentLimits()

        assert limits.timeout_ms > 0
        assert 0 < limits.max_output_bytes <= MAX_OUTPUT_BYTES
        assert limits.max_turns >= 1

    def test_zero_turns_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_turns must be at least 1"):
            AgentLimits(max_turns=0)

    def test_negative_cost_cap_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_cost_usd cannot be negative"):
            AgentLimits(max_cost_usd=-1.0)


class TestTurnAccumulator:
    def test_accumulates_in_order(self) -> None:
        acc = TurnAccumulator()

        acc.add("hello ")
        acc.add("world")

        assert acc.text == "hello world"
        assert acc.truncated is False
        assert acc.size_bytes == 11

    def test_truncates_at_the_byte_cap(self) -> None:
        acc = TurnAccumulator(max_output_bytes=5)

        accepted = acc.add("0123456789")

        assert accepted == "01234"
        assert acc.text == "01234"
        assert acc.truncated is True

    def test_nothing_is_accepted_after_truncation(self) -> None:
        acc = TurnAccumulator(max_output_bytes=2)
        acc.add("abcd")

        assert acc.add("more") == ""
        assert acc.text == "ab"

    def test_truncation_keeps_valid_utf8(self) -> None:
        # A cap that lands mid-character must not produce broken text.
        acc = TurnAccumulator(max_output_bytes=3)

        acc.add("\U0001f600\U0001f600")

        assert acc.truncated is True
        acc.text.encode("utf-8").decode("utf-8")
        assert acc.text == ""

    def test_empty_delta_is_a_noop(self) -> None:
        acc = TurnAccumulator()

        assert acc.add("") == ""
        assert acc.truncated is False


class TestErrorInfo:
    def test_exception_converts_to_safe_info(self) -> None:
        info = AgentTimeoutError("no output within 1000 ms").as_info(retryable=True)

        assert info.code == "agent_timeout"
        assert info.message == "no output within 1000 ms"
        assert info.retryable is True


class TestRegistry:
    def test_fake_backend_is_constructible(self) -> None:
        adapter = build_agent_adapter(AgentKind.FAKE)

        assert isinstance(adapter, FakeAgentAdapter)
        assert adapter.adapter_name == "fake"

    def test_accepts_a_plain_string(self) -> None:
        assert isinstance(build_agent_adapter("fake"), FakeAgentAdapter)

    @pytest.mark.parametrize(
        ("kind", "expected"), [("claude", "claude"), ("codex", "codex")]
    )
    def test_real_backends_are_constructible(self, kind: str, expected: str) -> None:
        # Construction must not spawn anything; that happens in start().
        adapter = build_agent_adapter(kind)

        assert adapter.adapter_name == expected
        assert isinstance(adapter, AgentAdapter)

    def test_executable_and_model_flow_through(self) -> None:
        adapter = build_agent_adapter(
            "claude", executable="/opt/claude", model="some-model"
        )

        assert adapter._executable == "/opt/claude"
        assert adapter._model == "some-model"

    def test_unknown_backend_lists_the_known_ones(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            build_agent_adapter("gpt-9")

        message = str(excinfo.value)
        assert "unknown agent" in message
        assert "fake" in message and "claude" in message and "codex" in message

    def test_every_declared_backend_is_implemented(self) -> None:
        assert set(implemented_agent_kinds()) == set(AgentKind)

    def test_fake_adapter_satisfies_the_protocol(self) -> None:
        assert isinstance(FakeAgentAdapter(), AgentAdapter)
