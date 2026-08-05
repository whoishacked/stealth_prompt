"""Tests for dock-driven session configuration and payload-only mode.

Two guarantees dominate here:

* the dock is a *client*. It proposes provider/model/mode; Python validates,
  applies, and freezes. Nothing it sends names an executable or a credential.
* payload-only means payload-only. No fill, no click, no press, ever.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from typing import Any, TypeVar

import pytest

from stealth_prompt.agents import FakeAgentAdapter
from stealth_prompt.agents.registry import OPENAI_KEY_VARS
from stealth_prompt.workbench.binding import BoundLocator, TargetBinding
from stealth_prompt.workbench.config import (
    RunMode,
    SafetySettings,
    TargetDataSharing,
    WorkbenchConfig,
    build_workbench_config,
)
from stealth_prompt.workbench.operations import (
    LocatorStrategy,
    SubmitAction,
    SubmitStrategy,
)
from stealth_prompt.workbench.protocol import decode
from stealth_prompt.workbench.session import WorkbenchSession

T = TypeVar("T")
LOCAL = "http://127.0.0.1:8765/chat"


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


class Sink:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def __call__(self, raw: str) -> None:
        self.frames.append(json.loads(raw))

    def of(self, type_: str) -> list[dict[str, Any]]:
        return [f for f in self.frames if f["type"] == type_]

    def last(self, type_: str) -> dict[str, Any]:
        matches = self.of(type_)
        assert matches, f"no {type_} in {[f['type'] for f in self.frames]}"
        return matches[-1]["payload"]

    @property
    def text(self) -> str:
        return json.dumps(self.frames)


def inbound(type_: str, payload: dict[str, Any] | None = None):
    return decode(json.dumps({"type": type_, "payload": payload or {}}), max_bytes=65536)


def a_binding() -> TargetBinding:
    return TargetBinding(
        target_origin="http://127.0.0.1:8765",
        input=BoundLocator(strategy=LocatorStrategy.CSS, value="#message"),
        submit_locator=BoundLocator(strategy=LocatorStrategy.CSS, value="#send"),
        submit_action=SubmitAction(strategy=SubmitStrategy.CLICK_BUTTON),
        response_locator=BoundLocator(
            strategy=LocatorStrategy.CSS, value=".assistant-message", pick="last"
        ),
    )


def make(
    *,
    mode: RunMode = RunMode.MANUAL,
    binding: TargetBinding | None = None,
    allow_ui: bool = True,
    sharing: TargetDataSharing = TargetDataSharing.NONE,
) -> tuple[WorkbenchSession, FakeAgentAdapter]:
    adapter = FakeAgentAdapter([["a payload"]])
    config = WorkbenchConfig(
        target_url=LOCAL,
        mode=mode,
        allow_auto_send=mode is RunMode.AUTO,
        allow_ui_configuration=allow_ui,
        safety=SafetySettings(target_data_sharing=sharing),
    )
    return WorkbenchSession(config, adapter, binding=binding), adapter


class TestCapabilities:
    def test_reports_providers_modes_and_sharing(self) -> None:
        session, _ = make()
        sink = Sink()

        run(session.handle(inbound("capabilities_request"), sink))

        payload = sink.last("capabilities")
        assert {p["kind"] for p in payload["providers"]} >= {
            "fake",
            "claude",
            "codex",
            "ollama",
            "openai",
        }
        assert "payload_only" in payload["modes"]
        assert set(payload["sharing"]) == {"none", "redacted", "full"}

    def test_capabilities_carry_no_secrets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(OPENAI_KEY_VARS[0], "sk-should-never-appear")
        session, _ = make()
        sink = Sink()

        run(session.handle(inbound("capabilities_request"), sink))

        assert "sk-should-never-appear" not in sink.text

    def test_health_separates_installed_from_authenticated(self) -> None:
        session, _ = make()
        sink = Sink()

        run(session.handle(inbound("provider_health_request"), sink))

        entries = sink.last("provider_health")["providers"]
        for entry in entries:
            assert "installed" in entry and "authenticated" in entry

    def test_health_carries_no_secrets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(OPENAI_KEY_VARS[0], "sk-should-never-appear")
        session, _ = make()
        sink = Sink()

        run(session.handle(inbound("provider_health_request"), sink))

        assert "sk-should-never-appear" not in sink.text


class TestModelList:
    def test_unknown_provider_is_reported_not_raised(self) -> None:
        session, _ = make()
        sink = Sink()

        run(session.handle(inbound("model_list_request", {"provider": "nope"}), sink))

        payload = sink.last("model_list")
        assert payload["models"] == []
        assert payload["error"]

    def test_a_provider_without_discovery_returns_an_empty_list(self) -> None:
        session, _ = make()
        sink = Sink()

        run(
            session.handle(inbound("model_list_request", {"provider": "claude"}), sink)
        )

        assert sink.last("model_list")["models"] == []

    def test_failure_stays_recoverable(self) -> None:
        # A model-list failure must never prevent configuring or running.
        session, _ = make()
        sink = Sink()

        run(session.handle(inbound("model_list_request", {"provider": "nope"}), sink))
        run(session.handle(inbound("capabilities_request"), sink))

        assert sink.of("capabilities")


class TestConfigureSession:
    def test_provider_and_model_are_applied(self) -> None:
        session, _ = make()
        sink = Sink()

        run(
            session.handle(
                inbound(
                    "configure_session", {"provider": "fake", "model": "some-model"}
                ),
                sink,
            )
        )

        assert sink.last("session_configured")["accepted"] is True
        assert session.config.agent.model == "some-model"

    def test_mode_and_sharing_are_applied(self) -> None:
        session, _ = make()
        sink = Sink()

        run(
            session.handle(
                inbound(
                    "configure_session",
                    {"mode": "supervised", "target_data_sharing": "redacted"},
                ),
                sink,
            )
        )

        assert session.config.mode is RunMode.SUPERVISED
        assert session.config.safety.target_data_sharing is TargetDataSharing.REDACTED

    def test_an_unsafe_model_name_is_refused(self) -> None:
        session, _ = make()
        sink = Sink()

        run(
            session.handle(
                inbound("configure_session", {"model": "bad; rm -rf /"}), sink
            )
        )

        payload = sink.last("session_configured")
        assert payload["accepted"] is False
        assert payload["code"] == "invalid_configuration"

    def test_an_unknown_provider_is_refused(self) -> None:
        session, _ = make()
        sink = Sink()

        run(
            session.handle(inbound("configure_session", {"provider": "evil"}), sink)
        )

        assert sink.last("session_configured")["accepted"] is False

    def test_an_unknown_mode_is_refused(self) -> None:
        session, _ = make()
        sink = Sink()

        run(session.handle(inbound("configure_session", {"mode": "godmode"}), sink))

        assert sink.last("session_configured")["accepted"] is False

    def test_the_dock_cannot_name_an_executable_or_endpoint(self) -> None:
        # These fields simply do not exist in the message contract; anything
        # extra is ignored rather than honoured.
        session, _ = make()
        sink = Sink()

        run(
            session.handle(
                inbound(
                    "configure_session",
                    {
                        "provider": "fake",
                        "executable": "/bin/sh",
                        "base_url": "http://evil.example",
                        "api_key": "sk-nope",
                    },
                ),
                sink,
            )
        )

        assert sink.last("session_configured")["accepted"] is True
        assert session.config.agent.base_url is None
        assert getattr(session.adapter, "adapter_name", "") == "fake"

    def test_ui_configuration_can_be_disabled(self) -> None:
        session, _ = make(allow_ui=False)
        sink = Sink()

        run(session.handle(inbound("configure_session", {"model": "x"}), sink))

        payload = sink.last("session_configured")
        assert payload["accepted"] is False
        assert payload["code"] == "ui_configuration_disabled"

    def test_changing_the_backend_closes_the_old_adapter(self) -> None:
        session, original = make()
        sink = Sink()

        run(
            session.handle(
                inbound("configure_session", {"provider": "fake", "model": "other"}),
                sink,
            )
        )

        assert original.closed is True
        assert session.adapter is not original

    def test_changing_only_the_objective_keeps_the_adapter(self) -> None:
        session, original = make()
        sink = Sink()

        run(
            session.handle(
                inbound("configure_session", {"objective": "a new goal"}), sink
            )
        )

        assert original.closed is False
        assert session.adapter is original

    def test_a_run_plan_follows_a_successful_change(self) -> None:
        session, _ = make(binding=a_binding())
        sink = Sink()

        run(session.handle(inbound("configure_session", {"mode": "auto"}), sink))

        plan = sink.last("run_plan")
        assert plan["mode"] == "auto"
        assert plan["binding_ready"] is True

    def test_plan_names_static_planning_under_sharing_none(self) -> None:
        session, _ = make(binding=a_binding())
        sink = Sink()

        run(
            session.handle(
                inbound(
                    "configure_session",
                    {"mode": "auto", "target_data_sharing": "none"},
                ),
                sink,
            )
        )

        assert sink.last("run_plan")["planning"] == "static"

    def test_plan_names_adaptive_planning_under_redacted(self) -> None:
        session, _ = make(binding=a_binding())
        sink = Sink()

        run(
            session.handle(
                inbound(
                    "configure_session",
                    {"mode": "auto", "target_data_sharing": "redacted"},
                ),
                sink,
            )
        )

        assert sink.last("run_plan")["planning"] == "adaptive"


class TestConfigurationFreeze:
    def test_configuration_is_rejected_once_a_run_starts(self) -> None:
        session, _ = make(mode=RunMode.AUTO, binding=a_binding())
        sink = Sink()

        async def scenario() -> None:
            await session.start_automated_run(sink)
            await session.handle(
                inbound("configure_session", {"model": "changed"}), sink
            )

        run(scenario())

        payload = sink.last("session_configured")
        assert payload["accepted"] is False
        assert payload["code"] == "configuration_frozen"

    def test_freeze_is_reported_in_capabilities(self) -> None:
        session, _ = make(mode=RunMode.AUTO, binding=a_binding())
        sink = Sink()

        async def scenario() -> None:
            await session.start_automated_run(sink)
            await session.handle(inbound("capabilities_request"), sink)

        run(scenario())

        assert sink.last("capabilities")["frozen"] is True


class TestPayloadOnlyMode:
    def test_send_is_refused_outright(self) -> None:
        session, _ = make(mode=RunMode.PAYLOAD_ONLY, binding=a_binding())
        sink = Sink()

        run(
            session.handle(
                inbound(
                    "send_approved",
                    {"approved": True, "payload": "p", "selector": "#send"},
                ),
                sink,
            )
        )

        assert sink.last("error")["code"] == "mutation_refused"
        assert session.turns == []

    def test_no_mutating_operation_is_ever_emitted(self) -> None:
        session, _ = make(mode=RunMode.PAYLOAD_ONLY, binding=a_binding())
        sink = Sink()

        async def scenario() -> None:
            await session.handle(inbound("operator_prompt", {"text": "go"}), sink)
            await session.handle(
                inbound("run_control", {"action": "capture"}), sink
            )
            await session.handle(
                inbound("target_response", {"text": "a reply"}), sink
            )
            await session.handle(inbound("operator_prompt", {"text": "again"}), sink)
            await session.handle(
                inbound(
                    "send_approved",
                    {"approved": True, "payload": "p", "selector": "#send"},
                ),
                sink,
            )

        run(scenario())

        assert "fill" not in session.emitted_operations
        assert "click" not in session.emitted_operations
        assert "press" not in session.emitted_operations
        # The only operation it may issue is a read.
        assert set(session.emitted_operations) <= {"extract"}

    def test_capture_uses_a_read_only_extract(self) -> None:
        session, _ = make(mode=RunMode.PAYLOAD_ONLY, binding=a_binding())
        sink = Sink()

        run(session.handle(inbound("run_control", {"action": "capture"}), sink))

        assert sink.last("perform_operation")["operation"] == "extract"

    def test_capture_needs_a_reply_locator(self) -> None:
        session, _ = make(mode=RunMode.PAYLOAD_ONLY)
        sink = Sink()

        run(session.handle(inbound("run_control", {"action": "capture"}), sink))

        assert sink.last("error")["code"] == "no_binding"

    def test_a_captured_reply_feeds_the_next_payload(self) -> None:
        session, adapter = make(
            mode=RunMode.PAYLOAD_ONLY,
            binding=a_binding(),
            sharing=TargetDataSharing.FULL,
        )
        sink = Sink()

        async def scenario() -> None:
            await session.handle(inbound("run_control", {"action": "capture"}), sink)
            await session.handle(
                inbound("target_response", {"text": "SECRET-REPLY-TEXT"}), sink
            )
            await session.handle(
                inbound("operator_prompt", {"text": "follow up"}), sink
            )

        run(scenario())

        assert "SECRET-REPLY-TEXT" in adapter.prompts[-1]

    def test_generation_repeats(self) -> None:
        session, adapter = make(mode=RunMode.PAYLOAD_ONLY, binding=a_binding())
        sink = Sink()

        async def scenario() -> None:
            for _ in range(3):
                await session.handle(
                    inbound("operator_prompt", {"text": "again"}), sink
                )

        run(scenario())

        assert len(adapter.prompts) == 3
        assert len(sink.of("agent_event")) >= 3

    def test_sharing_none_withholds_the_captured_reply(self) -> None:
        session, adapter = make(
            mode=RunMode.PAYLOAD_ONLY,
            binding=a_binding(),
            sharing=TargetDataSharing.NONE,
        )
        sink = Sink()

        async def scenario() -> None:
            await session.handle(inbound("run_control", {"action": "capture"}), sink)
            await session.handle(
                inbound("target_response", {"text": "SECRET-REPLY-TEXT"}), sink
            )
            await session.handle(inbound("operator_prompt", {"text": "go"}), sink)

        run(scenario())

        prompt = adapter.prompts[-1]
        assert "SECRET-REPLY-TEXT" not in prompt
        assert "not shared with you" in prompt


class TestCliConfigWiring:
    def test_model_reaches_agent_settings(self) -> None:
        config = build_workbench_config(
            target_url=LOCAL, provider="codex", model="gpt-5.6-sol"
        )

        assert config.agent.model == "gpt-5.6-sol"
        assert config.agent.provider == "codex"

    def test_model_appears_in_the_sanitized_snapshot(self) -> None:
        config = build_workbench_config(
            target_url=LOCAL, provider="claude", model="some-model"
        )

        described = config.describe()
        assert described["provider"] == "claude"
        assert described["agent_model"] == "some-model"

    def test_conflicting_provider_and_agent_are_refused(self) -> None:
        from stealth_prompt.workbench.config import WorkbenchConfigError

        with pytest.raises(WorkbenchConfigError, match="conflicts with"):
            build_workbench_config(
                target_url=LOCAL, provider="codex", agent="claude"
            )

    def test_agent_alias_still_selects_a_provider(self) -> None:
        config = build_workbench_config(target_url=LOCAL, agent="claude")

        assert config.agent.provider == "claude"

    def test_default_provider_is_fake(self) -> None:
        assert build_workbench_config(target_url=LOCAL).agent.provider == "fake"
