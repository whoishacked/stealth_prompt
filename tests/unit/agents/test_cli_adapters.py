"""Tests for the Claude and Codex CLI backends.

Both are driven against recorded protocol fixtures rather than a live CLI, so
they run offline, cost nothing, and pin the exact event shapes each adapter
claims to understand. The same lifecycle assertions applied to the fake backend
are re-applied here.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Coroutine, Sequence
from pathlib import Path
from typing import Any, TypeVar

import pytest

from stealth_prompt.agents.base import (
    AgentEvent,
    AgentEventKind,
    AgentProtocolError,
    AgentRequest,
    AgentStateError,
)
from stealth_prompt.agents.claude import (
    DISABLED_TOOLS,
    ClaudeAdapter,
    build_argv,
    extract_assistant_text,
    extract_delta,
    extract_usage,
    user_message,
)
from stealth_prompt.agents.claude import (
    REASONING_EFFORT as CLAUDE_REASONING_EFFORT,
)
from stealth_prompt.agents.claude import (
    SYSTEM_PROMPT as CLAUDE_SYSTEM_PROMPT,
)
from stealth_prompt.agents.codex import (
    APPROVAL_POLICY,
    BASE_INSTRUCTIONS,
    METHOD_INITIALIZE,
    METHOD_INITIALIZED,
    METHOD_THREAD_START,
    METHOD_TURN_INTERRUPT,
    METHOD_TURN_START,
    SANDBOX_MODE,
    CodexAdapter,
    app_server_argv,
    classify_notification,
    exec_argv,
    parse_model_list,
    schema_argv,
    thread_id_of,
    thread_start_params,
)
from stealth_prompt.agents.codex import (
    REASONING_EFFORT as CODEX_REASONING_EFFORT,
)
from stealth_prompt.agents.codex import extract_usage as codex_extract_usage
from stealth_prompt.agents.ollama import _post_json

T = TypeVar("T")


def test_ollama_stream_is_returned_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """The caller, not the request helper, owns the streaming response."""

    class Response:
        closed = False

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            self.closed = True

    response = Response()
    monkeypatch.setattr(
        "stealth_prompt.agents.ollama.urllib.request.urlopen",
        lambda *_args, **_kwargs: response,
    )

    returned = _post_json("http://127.0.0.1:11434/api/chat", {"model": "m"}, 1)

    assert returned is response
    assert response.closed is False


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


class FakeProcess:
    """Replays recorded stdout lines instead of spawning a child."""

    def __init__(
        self, argv: Sequence[str], *, cwd: str | None = None, env: Any = None
    ) -> None:
        self.argv = list(argv)
        self.cwd = cwd
        self.lines: list[str] = []
        self.written: list[Any] = []
        self.started = False
        self.terminated = False
        self.stdin_closed = False
        self.stderr_tail = ""

    def load(self, documents: Sequence[dict[str, Any]]) -> None:
        self.lines = [json.dumps(d) for d in documents]

    async def start(self) -> None:
        self.started = True

    async def write_json(self, document: Any) -> None:
        self.written.append(document)

    async def close_stdin(self) -> None:
        self.stdin_closed = True

    async def terminate(self) -> None:
        self.terminated = True

    async def read_json_lines(self) -> AsyncIterator[dict[str, Any]]:
        for line in self.lines:
            # Interleave noise the adapter must skip rather than interpret.
            text = line.strip()
            if not text.startswith("{"):
                continue
            yield json.loads(text)


def wire(adapter: Any, documents: Sequence[dict[str, Any]]) -> FakeProcess:
    """Attach a fake process preloaded with ``documents``."""
    process = FakeProcess(["fake"])
    process.load(documents)
    adapter._process_factory = lambda argv, cwd=None, env=None: process  # noqa: SLF001
    return process


async def collect(adapter: Any, request: AgentRequest) -> list[AgentEvent]:
    return [event async for event in adapter.send(request)]


def kinds(events: list[AgentEvent]) -> list[AgentEventKind]:
    return [e.kind for e in events]


def completed(events: list[AgentEvent]) -> AgentEvent:
    for event in events:
        if event.kind is AgentEventKind.MESSAGE_COMPLETED:
            return event
    raise AssertionError(f"no completion in {kinds(events)}")


# --------------------------------------------------------------------- Claude

CLAUDE_INIT = {"type": "system", "subtype": "init", "session_id": "sess-abc"}


def claude_delta(text: str) -> dict[str, Any]:
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": text},
        },
    }


CLAUDE_RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "usage": {"input_tokens": 120, "output_tokens": 45},
    "total_cost_usd": 0.0021,
}


class TestClaudeArgv:
    def test_uses_documented_streaming_flags(self) -> None:
        argv = build_argv()

        assert argv[0] == "claude"
        assert "--output-format" in argv
        assert argv[argv.index("--output-format") + 1] == "stream-json"
        assert argv[argv.index("--input-format") + 1] == "stream-json"
        assert "--include-partial-messages" in argv

    def test_builtin_tools_are_disabled(self) -> None:
        argv = build_argv()

        disallowed = argv[argv.index("--disallowed-tools") + 1]
        for tool in ("Bash", "Write", "WebFetch", "Task"):
            assert tool in disallowed
        assert disallowed == DISABLED_TOOLS

    def test_mcp_servers_are_disabled(self) -> None:
        argv = build_argv()

        assert "--strict-mcp-config" in argv
        assert json.loads(argv[argv.index("--mcp-config") + 1]) == {"mcpServers": {}}

    def test_payload_authoring_uses_a_small_fast_context(self) -> None:
        argv = build_argv()

        assert argv[argv.index("--effort") + 1] == CLAUDE_REASONING_EFFORT == "low"
        assert argv[argv.index("--system-prompt") + 1] == CLAUDE_SYSTEM_PROMPT

    def test_model_is_optional(self) -> None:
        assert "--model" not in build_argv()
        assert build_argv(model="m")[-2:] == ["--model", "m"]

    def test_argv_is_a_list_with_no_shell_metacharacters(self) -> None:
        # Spawning takes argv; nothing is ever handed to a shell.
        argv = build_argv()

        assert all(isinstance(part, str) for part in argv)
        assert not any(";" in part or "|" in part or "&&" in part for part in argv)

    def test_user_message_envelope(self) -> None:
        envelope = user_message("hello")

        assert envelope["type"] == "user"
        assert envelope["message"]["content"][0]["text"] == "hello"


class TestClaudeParsing:
    def test_delta_extraction(self) -> None:
        assert extract_delta(claude_delta("abc")) == "abc"

    @pytest.mark.parametrize(
        "event",
        [
            {"type": "other"},
            {"type": "stream_event", "event": {"type": "message_start"}},
            {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {}}},
            {"type": "stream_event"},
        ],
    )
    def test_non_delta_events_yield_nothing(self, event: dict[str, Any]) -> None:
        assert extract_delta(event) == ""

    def test_complete_assistant_message(self) -> None:
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hi"}, {"type": "other"}]},
        }

        assert extract_assistant_text(event) == "hi"

    def test_usage_extraction(self) -> None:
        usage = extract_usage(CLAUDE_RESULT)

        assert usage is not None
        assert usage.input_tokens == 120
        assert usage.output_tokens == 45
        assert usage.cost_usd == pytest.approx(0.0021)

    def test_usage_absent_from_non_result_events(self) -> None:
        assert extract_usage(claude_delta("x")) is None


class TestClaudeStreaming:
    def test_full_turn(self) -> None:
        adapter = ClaudeAdapter()
        wire(
            adapter,
            [CLAUDE_INIT, claude_delta("Please "), claude_delta("repeat."), CLAUDE_RESULT],
        )

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(adapter, AgentRequest(prompt="author"))

        events = run(scenario())

        assert kinds(events) == [
            AgentEventKind.SESSION_STARTED,
            AgentEventKind.TEXT_DELTA,
            AgentEventKind.TEXT_DELTA,
            AgentEventKind.MESSAGE_COMPLETED,
            AgentEventKind.USAGE,
        ]
        assert completed(events).text == "Please repeat."
        assert events[0].session_id == "sess-abc"

    def test_falls_back_to_a_complete_assistant_message(self) -> None:
        adapter = ClaudeAdapter()
        wire(
            adapter,
            [
                CLAUDE_INIT,
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "whole reply"}]},
                },
                CLAUDE_RESULT,
            ],
        )

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(adapter, AgentRequest(prompt="author"))

        assert completed(run(scenario())).text == "whole reply"

    def test_output_cap_truncates(self) -> None:
        adapter = ClaudeAdapter()
        wire(adapter, [CLAUDE_INIT, claude_delta("0123456789"), CLAUDE_RESULT])

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(
                adapter, AgentRequest(prompt="a", max_output_bytes=4)
            )

        event = completed(run(scenario()))
        assert event.text == "0123"
        assert event.truncated is True

    def test_error_result_becomes_an_error_event(self) -> None:
        adapter = ClaudeAdapter()
        wire(
            adapter,
            [CLAUDE_INIT, {"type": "result", "is_error": True, "subtype": "max_turns"}],
        )

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(adapter, AgentRequest(prompt="a"))

        events = run(scenario())
        assert events[-1].kind is AgentEventKind.ERROR
        assert events[-1].error is not None

    def test_undocumented_lines_are_skipped_not_parsed(self) -> None:
        adapter = ClaudeAdapter()
        wire(
            adapter,
            [
                CLAUDE_INIT,
                {"type": "totally_unknown_event", "text": "ignore me"},
                claude_delta("real"),
                CLAUDE_RESULT,
            ],
        )

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(adapter, AgentRequest(prompt="a"))

        assert completed(run(scenario())).text == "real"

    def test_prompt_is_written_as_a_stream_json_user_turn(self) -> None:
        adapter = ClaudeAdapter()
        process = wire(adapter, [CLAUDE_INIT, CLAUDE_RESULT])

        async def scenario() -> None:
            await adapter.start()
            await collect(adapter, AgentRequest(prompt="the prompt"))

        run(scenario())

        assert process.written[0]["message"]["content"][0]["text"] == "the prompt"


class TestClaudeLifecycle:
    def test_send_before_start_is_rejected(self) -> None:
        adapter = ClaudeAdapter()

        with pytest.raises(AgentStateError, match="call start"):
            run(collect(adapter, AgentRequest(prompt="a")))

    def test_close_terminates_the_child_and_is_idempotent(self) -> None:
        adapter = ClaudeAdapter()
        process = wire(adapter, [CLAUDE_INIT, CLAUDE_RESULT])

        async def scenario() -> None:
            await adapter.start()
            await adapter.close()
            await adapter.close()

        run(scenario())

        assert process.terminated is True
        assert process.stdin_closed is True

    def test_send_after_close_is_rejected(self) -> None:
        adapter = ClaudeAdapter()
        wire(adapter, [CLAUDE_INIT])

        async def scenario() -> None:
            await adapter.start()
            await adapter.close()
            await collect(adapter, AgentRequest(prompt="a"))

        with pytest.raises(AgentStateError, match="closed"):
            run(scenario())

    def test_interrupt_ends_the_stream(self) -> None:
        adapter = ClaudeAdapter()
        wire(
            adapter,
            [CLAUDE_INIT, claude_delta("one"), claude_delta("two"), CLAUDE_RESULT],
        )

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            seen: list[AgentEvent] = []
            async for event in adapter.send(AgentRequest(prompt="a")):
                seen.append(event)
                if event.kind is AgentEventKind.TEXT_DELTA:
                    await adapter.interrupt()
            return seen

        events = run(scenario())
        assert events[-1].kind is AgentEventKind.INTERRUPTED

    def test_preflight_reports_a_missing_executable(self) -> None:
        result = run(ClaudeAdapter(executable="definitely-not-real-xyz").preflight())

        assert result.available is False
        assert result.remedy


# ---------------------------------------------------------------------- Codex
#
# Every fixture below mirrors the schema the installed binary emits
# (codex-cli 0.146.0-alpha.3.1). The field names were read out of
# `codex app-server generate-json-schema`, not from memory:
#   thread/start takes `sandbox`, the thread id is nested at `thread.id`,
#   and turn/interrupt needs both `threadId` and `turnId`.
# tests/fixtures/codex/app_server_v2_subset.json holds the generated subset and
# TestCodexSchemaContract asserts this module still agrees with it.


def rpc_result(request_id: int, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def thread_object(thread_id: str = "thr-42") -> dict[str, Any]:
    """A Thread as the schema defines it: the id lives inside the object."""
    return {
        "id": thread_id,
        "cliVersion": "0.146.0-alpha.3.1",
        "createdAt": 0,
        "updatedAt": 0,
        "cwd": "/tmp",
        "ephemeral": True,
        "modelProvider": "openai",
        "preview": "",
        "sessionId": "sess-1",
        "source": {"kind": "app"},
        "status": "active",
        "turns": [],
    }


CODEX_INIT_OK = rpc_result(1, {"userAgent": "codex/0.146.0-alpha.3.1"})
CODEX_THREAD_OK = rpc_result(
    2,
    {
        "thread": thread_object(),
        "model": "gpt-5-codex",
        "modelProvider": "openai",
        "sandbox": {"mode": "read-only"},
        "approvalPolicy": "never",
        "approvalsReviewer": "user",
        "cwd": "/tmp",
    },
)
CODEX_THREAD_STARTED = {
    "jsonrpc": "2.0",
    "method": "thread/started",
    "params": {"thread": thread_object()},
}
CODEX_TURN_STARTED = {
    "jsonrpc": "2.0",
    "method": "turn/started",
    "params": {"threadId": "thr-42", "turn": {"id": "turn-7", "status": "inProgress"}},
}


def codex_delta(text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "item/agentMessage/delta",
        "params": {
            "delta": text,
            "itemId": "item-1",
            "threadId": "thr-42",
            "turnId": "turn-7",
        },
    }


def codex_item(text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "item/completed",
        "params": {
            "completedAtMs": 0,
            "threadId": "thr-42",
            "turnId": "turn-7",
            "item": {"type": "agentMessage", "id": "item-1", "text": text},
        },
    }


CODEX_USAGE = {
    "jsonrpc": "2.0",
    "method": "thread/tokenUsage/updated",
    "params": {
        "threadId": "thr-42",
        "usage": {
            "total": {
                "inputTokens": 10,
                "outputTokens": 20,
                "cachedInputTokens": 0,
                "reasoningOutputTokens": 0,
                "totalTokens": 30,
            },
            "last": {
                "inputTokens": 10,
                "outputTokens": 20,
                "cachedInputTokens": 0,
                "reasoningOutputTokens": 0,
                "totalTokens": 30,
            },
        },
    },
}

CODEX_DONE = {
    "jsonrpc": "2.0",
    "method": "turn/completed",
    "params": {"threadId": "thr-42", "turn": {"id": "turn-7", "status": "completed"}},
}

CODEX_SETUP = [CODEX_INIT_OK, CODEX_THREAD_OK]


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    """The definitions the installed binary generated."""
    path = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "codex"
        / "app_server_v2_subset.json"
    )
    return json.loads(path.read_text())["definitions"]


class TestCodexSchemaContract:
    """The adapter must agree with the schema the real binary generates."""

    def test_thread_start_sends_only_declared_fields(
        self, schema: dict[str, Any]
    ) -> None:
        declared = set(schema["ThreadStartParams"]["properties"])

        sent = set(thread_start_params(model="m", cwd="/tmp"))

        assert sent <= declared, f"undeclared fields: {sorted(sent - declared)}"

    def test_the_old_guessed_field_names_are_absent(
        self, schema: dict[str, Any]
    ) -> None:
        declared = set(schema["ThreadStartParams"]["properties"])

        # These were in an earlier draft and do not exist. A server that
        # ignores unknown fields would have silently run under its default
        # sandbox instead of read-only.
        assert "sandboxMode" not in declared
        assert "skipGitRepoCheck" not in declared
        assert "sandbox" in declared

    def test_sandbox_and_approval_values_are_in_the_enums(
        self, schema: dict[str, Any]
    ) -> None:
        assert SANDBOX_MODE in schema["SandboxMode"]["enum"]
        approval = schema["AskForApproval"]["oneOf"][0]["enum"]
        assert APPROVAL_POLICY in approval

    def test_thread_id_is_nested_not_top_level(self, schema: dict[str, Any]) -> None:
        start = schema["ThreadStartResponse"]
        started = schema["ThreadStartedNotification"]

        assert "thread" in start["properties"]
        assert "threadId" not in start["properties"]
        assert "thread" in started["properties"]
        assert "threadId" not in started["properties"]
        assert "id" in schema["Thread"]["properties"]

    def test_turn_start_requires_thread_id_and_input(
        self, schema: dict[str, Any]
    ) -> None:
        required = set(schema["TurnStartParams"]["required"])

        assert required == {"threadId", "input"}

    def test_turn_interrupt_requires_both_ids(self, schema: dict[str, Any]) -> None:
        assert set(schema["TurnInterruptParams"]["required"]) == {"threadId", "turnId"}

    def test_agent_message_delta_field_names(self, schema: dict[str, Any]) -> None:
        properties = schema["AgentMessageDeltaNotification"]["properties"]

        assert "delta" in properties
        assert {"itemId", "threadId", "turnId"} <= set(properties)

    def test_effective_model_is_reported_by_thread_start(
        self, schema: dict[str, Any]
    ) -> None:
        required = set(schema["ThreadStartResponse"]["required"])

        assert "model" in required
        assert "modelProvider" in required


class TestCodexArgv:
    def test_app_server_is_the_primary_transport(self) -> None:
        assert app_server_argv() == ["codex", "app-server"]

    def test_websocket_transport_is_never_requested(self) -> None:
        argv = " ".join(app_server_argv())
        assert "websocket" not in argv and "--ws" not in argv

    def test_schema_generation_targets_a_caller_supplied_directory(self) -> None:
        argv = schema_argv("/tmp/out")

        assert argv[:3] == ["codex", "app-server", "generate-json-schema"]
        assert argv[argv.index("--out") + 1] == "/tmp/out"

    def test_exec_fallback_uses_json_and_a_sandbox(self) -> None:
        argv = exec_argv()

        assert argv[:4] == ["codex", "exec", "--json", "--skip-git-repo-check"]
        assert argv[argv.index("--sandbox") + 1] == SANDBOX_MODE
        assert argv[-1] == "-"


class TestCodexSandbox:
    def test_thread_is_read_only_with_no_approvals(self) -> None:
        params = thread_start_params()

        assert params["sandbox"] == "read-only"
        assert params["approvalPolicy"] == "never"
        assert params["baseInstructions"] == BASE_INSTRUCTIONS

    def test_model_and_cwd_are_optional(self) -> None:
        assert "model" not in thread_start_params()
        assert thread_start_params(model="m")["model"] == "m"


class TestCodexParsing:
    def test_thread_id_is_read_from_the_nested_object(self) -> None:
        assert thread_id_of({"thread": {"id": "thr-9"}}) == "thr-9"

    def test_top_level_thread_id_is_not_accepted(self) -> None:
        # Guards against reintroducing the guessed shape.
        assert thread_id_of({"threadId": "thr-9"}) == ""

    def test_thread_started_notification(self) -> None:
        assert classify_notification(CODEX_THREAD_STARTED) == ("thread", "thr-42")

    def test_turn_started_yields_the_turn_id(self) -> None:
        assert classify_notification(CODEX_TURN_STARTED) == ("turn", "turn-7")

    def test_delta_notification(self) -> None:
        assert classify_notification(codex_delta("abc")) == ("delta", "abc")

    def test_completed_item_notification(self) -> None:
        assert classify_notification(codex_item("whole")) == ("message", "whole")

    def test_non_message_items_are_ignored(self) -> None:
        event = {
            "method": "item/completed",
            "params": {"item": {"type": "commandExecution", "id": "i"}},
        }

        assert classify_notification(event) == ("", "")

    def test_turn_completed_notification(self) -> None:
        assert classify_notification(CODEX_DONE)[0] == "completed"

    def test_error_notification_carries_the_message(self) -> None:
        event = {
            "method": "error",
            "params": {
                "threadId": "thr-42",
                "turnId": "turn-7",
                "willRetry": False,
                "error": {"message": "model unavailable"},
            },
        }

        assert classify_notification(event) == ("failed", "model unavailable")

    def test_usage_comes_from_its_own_notification(self) -> None:
        usage = codex_extract_usage(CODEX_USAGE)

        assert usage is not None
        assert usage.input_tokens == 10
        assert usage.output_tokens == 20

    def test_turn_completed_carries_no_usage(self) -> None:
        assert codex_extract_usage(CODEX_DONE) is None

    @pytest.mark.parametrize(
        "event",
        [
            {"method": "codex/event/agent_message_delta", "params": {"delta": "x"}},
            {"method": "newThread"},
            {"method": "sendUserTurn"},
            {"method": "turn/failed", "params": {}},
            {"no_method": True},
            {"method": 5},
        ],
    )
    def test_legacy_and_unknown_names_are_ignored(self, event: dict[str, Any]) -> None:
        assert classify_notification(event) == ("", "")

    def test_model_list_is_flattened(self) -> None:
        models = parse_model_list(
            {
                "data": [
                    {"id": "gpt-5", "displayName": "GPT-5", "isDefault": True},
                    {"id": "hidden-one", "displayName": "H", "hidden": True},
                    {"id": "gpt-4", "displayName": "GPT-4"},
                ]
            }
        )

        assert [m["id"] for m in models] == ["gpt-5", "gpt-4"]
        assert models[0]["default"] is True

    def test_model_list_tolerates_junk(self) -> None:
        assert parse_model_list({"data": "not a list"}) == []
        assert parse_model_list(None) == []


class TestCodexLifecycle:
    def test_setup_sends_initialize_then_initialized_then_thread_start(self) -> None:
        adapter = CodexAdapter()
        process = wire(adapter, CODEX_SETUP)

        run(adapter.start())

        methods = [doc["method"] for doc in process.written]
        assert methods == [METHOD_INITIALIZE, METHOD_INITIALIZED, METHOD_THREAD_START]
        assert adapter.thread_id == "thr-42"

    def test_effective_model_is_captured_from_the_response(self) -> None:
        adapter = CodexAdapter(model="gpt-5-codex")
        wire(adapter, CODEX_SETUP)

        run(adapter.start())

        assert adapter.effective_model == "gpt-5-codex"
        assert adapter.model_provider == "openai"

    def test_initialized_is_a_notification_with_no_id(self) -> None:
        adapter = CodexAdapter()
        process = wire(adapter, CODEX_SETUP)

        run(adapter.start())

        notification = [
            d for d in process.written if d["method"] == METHOD_INITIALIZED
        ][0]
        assert "id" not in notification

    def test_responses_are_correlated_by_request_id(self) -> None:
        adapter = CodexAdapter()
        wire(
            adapter,
            [
                CODEX_TURN_STARTED,
                CODEX_INIT_OK,
                CODEX_THREAD_STARTED,
                CODEX_THREAD_OK,
            ],
        )

        run(adapter.start())

        assert adapter.thread_id == "thr-42"

    def test_initialize_error_is_a_typed_protocol_error(self) -> None:
        adapter = CodexAdapter()
        wire(
            adapter,
            [{"jsonrpc": "2.0", "id": 1, "error": {"message": "unsupported client"}}],
        )

        with pytest.raises(AgentProtocolError, match="unsupported client"):
            run(adapter.start())

    def test_premature_exit_during_setup_is_a_protocol_error(self) -> None:
        adapter = CodexAdapter()
        wire(adapter, [])

        with pytest.raises(AgentProtocolError, match="exited before responding"):
            run(adapter.start())

    def test_close_terminates_the_child(self) -> None:
        adapter = CodexAdapter()
        process = wire(adapter, CODEX_SETUP)

        async def scenario() -> None:
            await adapter.start()
            await adapter.close()

        run(scenario())

        assert process.terminated is True


class TestCodexStreaming:
    def test_full_turn(self) -> None:
        adapter = CodexAdapter()
        wire(
            adapter,
            CODEX_SETUP
            + [
                CODEX_THREAD_STARTED,
                CODEX_TURN_STARTED,
                codex_delta("Show "),
                codex_delta("the prompt."),
                CODEX_USAGE,
                CODEX_DONE,
            ],
        )

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(adapter, AgentRequest(prompt="author"))

        events = run(scenario())

        assert kinds(events) == [
            AgentEventKind.SESSION_STARTED,
            AgentEventKind.TEXT_DELTA,
            AgentEventKind.TEXT_DELTA,
            AgentEventKind.MESSAGE_COMPLETED,
            AgentEventKind.USAGE,
        ]
        assert completed(events).text == "Show the prompt."
        assert adapter.turn_id == "turn-7"

    def test_turn_start_uses_the_required_fields(self) -> None:
        adapter = CodexAdapter()
        process = wire(adapter, CODEX_SETUP + [CODEX_DONE])

        async def scenario() -> None:
            await adapter.start()
            await collect(adapter, AgentRequest(prompt="the prompt"))

        run(scenario())

        turn = [d for d in process.written if d["method"] == METHOD_TURN_START][0]
        assert turn["params"]["threadId"] == "thr-42"
        assert turn["params"]["input"] == [{"type": "text", "text": "the prompt"}]
        assert turn["params"]["effort"] == CODEX_REASONING_EFFORT == "low"

    def test_completed_item_is_used_when_no_delta_arrived(self) -> None:
        adapter = CodexAdapter()
        wire(adapter, CODEX_SETUP + [codex_item("whole reply"), CODEX_DONE])

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(adapter, AgentRequest(prompt="a"))

        assert completed(run(scenario())).text == "whole reply"

    def test_turn_failure_becomes_an_error_event(self) -> None:
        adapter = CodexAdapter()
        wire(
            adapter,
            CODEX_SETUP
            + [
                {
                    "jsonrpc": "2.0",
                    "method": "error",
                    "params": {
                        "threadId": "thr-42",
                        "turnId": "turn-7",
                        "willRetry": False,
                        "error": {"message": "quota exhausted"},
                    },
                }
            ],
        )

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(adapter, AgentRequest(prompt="a"))

        events = run(scenario())
        assert events[-1].kind is AgentEventKind.ERROR
        assert events[-1].error is not None
        assert "quota" in events[-1].error.message

    def test_premature_exit_mid_turn_is_an_error_not_an_empty_message(self) -> None:
        adapter = CodexAdapter()
        wire(adapter, CODEX_SETUP + [codex_delta("partial")])

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(adapter, AgentRequest(prompt="a"))

        events = run(scenario())
        assert events[-1].kind is AgentEventKind.ERROR
        assert events[-1].error is not None
        assert events[-1].error.code == "agent_protocol"

    def test_interrupt_sends_both_ids(self) -> None:
        adapter = CodexAdapter()
        process = wire(
            adapter,
            CODEX_SETUP
            + [CODEX_THREAD_STARTED, CODEX_TURN_STARTED, codex_delta("x"), CODEX_DONE],
        )

        async def scenario() -> None:
            await adapter.start()
            async for event in adapter.send(AgentRequest(prompt="a")):
                if event.kind is AgentEventKind.TEXT_DELTA:
                    await adapter.interrupt()

        run(scenario())

        interrupts = [
            d for d in process.written if d.get("method") == METHOD_TURN_INTERRUPT
        ]
        assert interrupts, "no turn/interrupt was sent"
        assert interrupts[0]["params"] == {"threadId": "thr-42", "turnId": "turn-7"}

    def test_output_cap_truncates(self) -> None:
        adapter = CodexAdapter()
        wire(adapter, CODEX_SETUP + [codex_delta("0123456789"), CODEX_DONE])

        async def scenario() -> list[AgentEvent]:
            await adapter.start()
            return await collect(
                adapter, AgentRequest(prompt="a", max_output_bytes=4)
            )

        event = completed(run(scenario()))
        assert event.text == "0123"
        assert event.truncated is True


class TestCodexModelList:
    def test_models_are_discovered(self) -> None:
        adapter = CodexAdapter()
        wire(
            adapter,
            CODEX_SETUP
            + [
                rpc_result(
                    3, {"data": [{"id": "gpt-5", "displayName": "GPT-5", "isDefault": True}]}
                )
            ],
        )

        async def scenario() -> list[dict[str, Any]]:
            await adapter.start()
            return await adapter.list_models()

        models = run(scenario())

        assert models == [{"id": "gpt-5", "label": "GPT-5", "default": True}]

    def test_a_model_list_error_is_recoverable(self) -> None:
        # A failed model list must never block a run.
        adapter = CodexAdapter()
        wire(
            adapter,
            CODEX_SETUP
            + [{"jsonrpc": "2.0", "id": 3, "error": {"message": "unsupported"}}],
        )

        async def scenario() -> list[dict[str, Any]]:
            await adapter.start()
            return await adapter.list_models()

        assert run(scenario()) == []
