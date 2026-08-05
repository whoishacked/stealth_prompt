"""Regression tests for the provider-authority and transactional-switch bugs.

Each test here failed before this change. Two defects made a real Claude or
Codex run unreachable through the dock:

* ``preflight_problems()`` asked the legacy ``AgentKind``, which has no member
  for Ollama or OpenAI, so both were silently classified as Fake and blocked
  from using redacted or full sharing;
* ``apply_configuration()`` wrote the new config before building the adapter,
  so a failed switch left the config naming a backend that never ran -- an
  artifact could claim OpenAI while Fake authored every payload.
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


@pytest.fixture(autouse=True)
def _cli_backends_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the Claude and Codex CLIs are installed.

    These tests are about provider authority and transactional switching, not
    about what happens to be on the machine's PATH. Left unpatched they pass on
    a developer box that has the CLIs and fail in CI that does not, which is
    exactly backwards for a regression suite.
    """
    from stealth_prompt.agents import registry

    cli_kinds = {registry.ProviderKind.CLAUDE, registry.ProviderKind.CODEX}
    monkeypatch.setattr(
        registry,
        "resolve_executable",
        lambda kind: f"/usr/bin/{kind.value}" if kind in cli_kinds else None,
    )
    registry.clear_health_cache()


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


def make_session(
    *, sharing: TargetDataSharing = TargetDataSharing.NONE, **kwargs: Any
) -> tuple[WorkbenchSession, FakeAgentAdapter]:
    adapter = FakeAgentAdapter([["a payload"]])
    config = WorkbenchConfig(
        target_url=LOCAL,
        safety=SafetySettings(target_data_sharing=sharing),
        **kwargs,
    )
    return WorkbenchSession(config, adapter, binding=a_binding()), adapter


class TestProviderIsAuthoritative:
    """Bug 1: registry-only providers were classified as Fake."""

    @pytest.mark.parametrize("provider", ["ollama", "openai"])
    def test_registry_only_providers_are_not_fake(self, provider: str) -> None:
        config = build_workbench_config(
            target_url=LOCAL, provider=provider, target_data_sharing="redacted"
        )

        assert config.agent.provider == provider
        assert config.agent.is_real_backend is True
        assert config.preflight_problems() == ()

    @pytest.mark.parametrize("provider", ["claude", "codex"])
    def test_cli_providers_also_accept_sharing(self, provider: str) -> None:
        config = build_workbench_config(
            target_url=LOCAL, provider=provider, target_data_sharing="full"
        )

        assert config.preflight_problems() == ()

    def test_fake_still_refuses_sharing(self) -> None:
        # The check itself is right; it was asking the wrong question.
        config = build_workbench_config(
            target_url=LOCAL, provider="fake", target_data_sharing="redacted"
        )

        problems = config.preflight_problems()
        assert any("fake backend" in problem for problem in problems)

    def test_external_classification_uses_the_registry(self) -> None:
        local = build_workbench_config(target_url=LOCAL, provider="ollama")
        remote = build_workbench_config(target_url=LOCAL, provider="openai")

        assert local.agent.is_external is False
        assert remote.agent.is_external is True

    def test_describe_reports_the_provider_not_the_shim(self) -> None:
        described = build_workbench_config(
            target_url=LOCAL, provider="ollama"
        ).describe()

        assert described["agent"] == "ollama"
        assert described["provider"] == "ollama"

    def test_kind_shim_never_misreports_a_real_backend_as_usable_fake(self) -> None:
        # The shim still returns FAKE for ollama/openai because AgentKind has
        # no member for them. That is exactly why nothing may branch on it.
        config = build_workbench_config(target_url=LOCAL, provider="ollama")

        assert config.agent.kind.value == "fake"
        assert config.agent.provider == "ollama"
        assert config.agent.is_real_backend is True


class TestUiProviderSwitch:
    """Bug 1, as reached through the dock."""

    @pytest.mark.parametrize("provider", ["claude", "codex"])
    def test_switch_from_fake_produces_the_right_adapter(
        self, provider: str
    ) -> None:
        session, original = make_session()
        sink = Sink()

        run(
            session.handle(
                inbound(
                    "configure_session",
                    {"provider": provider, "target_data_sharing": "redacted"},
                ),
                sink,
            )
        )

        assert sink.last("session_configured")["accepted"] is True
        assert session.config.agent.provider == provider
        assert session.adapter.adapter_name == provider
        assert session.adapter is not original
        # And crucially: no leftover fake-provider preflight error.
        assert session.config.preflight_problems() == ()

    @pytest.mark.parametrize("provider", ["claude", "codex"])
    def test_switched_session_is_startable(self, provider: str) -> None:
        session, _ = make_session()
        sink = Sink()

        run(
            session.handle(
                inbound(
                    "configure_session",
                    {
                        "provider": provider,
                        "target_data_sharing": "redacted",
                        "mode": "auto",
                    },
                ),
                sink,
            )
        )

        blockers = {c["key"] for c in session.readiness().to_dict()["blockers"]}
        assert "sharing" not in blockers
        assert "configuration" not in blockers


class TestTransactionalSwitch:
    """Bug 2: a failed switch must change nothing."""

    @pytest.fixture(autouse=True)
    def _no_openai_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in OPENAI_KEY_VARS:
            monkeypatch.delenv(name, raising=False)

    def test_failed_switch_leaves_config_and_adapter_untouched(self) -> None:
        session, original = make_session()
        sink = Sink()

        run(
            session.handle(
                inbound(
                    "configure_session",
                    {"provider": "openai", "model": "gpt-test"},
                ),
                sink,
            )
        )

        payload = sink.last("session_configured")
        assert payload["accepted"] is False
        # Nothing moved.
        assert session.config.agent.provider == "fake"
        assert session.config.agent.model is None
        assert session.adapter is original
        assert original.closed is False

    def test_failed_switch_reports_the_unchanged_configuration(self) -> None:
        session, _ = make_session()
        sink = Sink()

        run(
            session.handle(
                inbound("configure_session", {"provider": "openai"}), sink
            )
        )

        current = sink.last("session_configured")["current"]
        assert current["provider"] == "fake"

    def test_the_previous_provider_stays_usable(self) -> None:
        session, original = make_session()
        sink = Sink()

        async def scenario() -> None:
            await session.handle(
                inbound("configure_session", {"provider": "openai"}), sink
            )
            # The old backend must still work afterwards.
            await session.handle(inbound("run_control", {"action": "generate"}), sink)

        run(scenario())

        assert original.prompts, "the surviving adapter did not run"
        assert session.adapter is original

    def test_a_successful_switch_closes_the_old_adapter(self) -> None:
        session, original = make_session()
        sink = Sink()

        run(
            session.handle(
                inbound("configure_session", {"provider": "claude"}), sink
            )
        )

        assert original.closed is True
        assert session.adapter is not original

    def test_configuration_generation_advances_only_on_success(self) -> None:
        session, _ = make_session()
        sink = Sink()
        before = session.configuration_generation

        run(session.handle(inbound("configure_session", {"provider": "openai"}), sink))
        after_failure = session.configuration_generation
        run(session.handle(inbound("configure_session", {"provider": "claude"}), sink))

        assert after_failure == before
        assert session.configuration_generation == before + 1

    def test_a_rejected_switch_never_claims_a_backend_that_did_not_run(self) -> None:
        # The audit-integrity property: requested and actual must agree.
        session, _ = make_session()
        sink = Sink()

        run(session.handle(inbound("configure_session", {"provider": "openai"}), sink))
        document = session.result_document()

        adapter = document["adapter"]
        assert adapter["requested_provider"] == adapter["adapter_name"]


class TestEffectiveModelRecording:
    def test_manual_generation_records_the_effective_model(self) -> None:
        session, adapter = make_session(mode=RunMode.MANUAL)
        adapter.effective_model = "fake-model-v2"
        sink = Sink()

        run(session.handle(inbound("run_control", {"action": "generate"}), sink))

        assert session.config.agent.effective_model == "fake-model-v2"

    def test_payload_only_generation_records_the_effective_model(self) -> None:
        session, adapter = make_session(mode=RunMode.PAYLOAD_ONLY)
        adapter.effective_model = "fake-model-v3"
        sink = Sink()

        run(session.handle(inbound("run_control", {"action": "generate"}), sink))

        assert session.config.agent.effective_model == "fake-model-v3"

    def test_effective_model_reaches_the_result(self) -> None:
        session, adapter = make_session(mode=RunMode.MANUAL)
        adapter.effective_model = "fake-model-v4"
        sink = Sink()

        run(session.handle(inbound("run_control", {"action": "generate"}), sink))

        assert session.result_document()["adapter"]["effective_model"] == "fake-model-v4"


class TestModelRendering:
    def test_capabilities_carry_the_configured_model(self) -> None:
        adapter = FakeAgentAdapter()
        config = build_workbench_config(
            target_url=LOCAL, provider="codex", model="gpt-5.6-sol"
        )
        session = WorkbenchSession(config, adapter)
        sink = Sink()

        run(session.handle(inbound("capabilities_request"), sink))

        assert sink.last("capabilities")["current"]["model"] == "gpt-5.6-sol"

    def test_model_list_echoes_its_correlation_id(self) -> None:
        session, _ = make_session()
        sink = Sink()

        run(
            session.handle(
                inbound(
                    "model_list_request", {"provider": "claude", "request_id": "7"}
                ),
                sink,
            )
        )

        payload = sink.last("model_list")
        assert payload["request_id"] == "7"
        assert payload["provider"] == "claude"

    def test_model_list_for_an_unknown_provider_still_echoes(self) -> None:
        session, _ = make_session()
        sink = Sink()

        run(
            session.handle(
                inbound("model_list_request", {"provider": "nope", "request_id": "9"}),
                sink,
            )
        )

        assert sink.last("model_list")["request_id"] == "9"


class TestAutomaticFirstPayload:
    def test_generation_needs_no_operator_instruction(self) -> None:
        session, adapter = make_session(mode=RunMode.MANUAL)
        sink = Sink()

        run(session.handle(inbound("run_control", {"action": "generate"}), sink))

        assert adapter.prompts, "no planner call was made"
        assert session.pending_payload

    def test_the_first_prompt_carries_the_objective_and_no_reply(self) -> None:
        session, adapter = make_session(mode=RunMode.MANUAL)
        sink = Sink()

        run(session.handle(inbound("run_control", {"action": "generate"}), sink))

        prompt = adapter.prompts[0]
        assert session.config.safety.objective[:40] in prompt
        assert "no previous target" in prompt.lower()

    def test_an_optional_instruction_is_passed_through(self) -> None:
        session, adapter = make_session(mode=RunMode.MANUAL)
        sink = Sink()

        run(
            session.handle(
                inbound(
                    "run_control",
                    {"action": "generate", "instruction": "try base64 encoding"},
                ),
                sink,
            )
        )

        assert "try base64 encoding" in adapter.prompts[0]

    def test_oracle_presence_is_mentioned_without_the_protected_value(self) -> None:
        from stealth_prompt.oracles import Oracle, OracleType

        adapter = FakeAgentAdapter()
        session = WorkbenchSession(
            WorkbenchConfig(target_url=LOCAL),
            adapter,
            oracles=[
                Oracle(
                    oracle_id="canary",
                    oracle_type=OracleType.FRAGMENT,
                    pattern="SP_CANARY_SECRETVALUE",
                )
            ],
            binding=a_binding(),
        )
        sink = Sink()

        run(session.handle(inbound("run_control", {"action": "generate"}), sink))

        prompt = adapter.prompts[0]
        assert "deterministic disclosure oracle" in prompt
        assert "SP_CANARY_SECRETVALUE" not in prompt

    def test_start_in_manual_mode_authors_a_payload(self) -> None:
        session, adapter = make_session(mode=RunMode.MANUAL)
        sink = Sink()

        run(session.handle(inbound("run_control", {"action": "start"}), sink))

        assert adapter.prompts


class TestStartCarriesTheDraft:
    def test_start_applies_the_configuration_draft(self) -> None:
        # Stays on the fake backend: starting a real CLI would spawn a child
        # process and, for a network backend, cost money. Provider switching
        # itself is covered by TestUiProviderSwitch.
        session, _ = make_session()
        sink = Sink()

        run(
            session.handle(
                inbound(
                    "run_control",
                    {
                        "action": "start",
                        "config": {
                            "provider": "fake",
                            "mode": "manual",
                            "objective": "a specific goal",
                            "max_turns": 5,
                        },
                    },
                ),
                sink,
            )
        )

        assert session.config.safety.objective == "a specific goal"
        assert session.config.safety.max_turns == 5

    def test_a_draft_switch_to_a_real_backend_is_applied_without_starting_it(
        self,
    ) -> None:
        # configure_session builds the adapter but never starts it, so no
        # child process is spawned here.
        session, _ = make_session()
        sink = Sink()

        run(
            session.handle(
                inbound(
                    "configure_session",
                    {"provider": "claude", "target_data_sharing": "redacted"},
                ),
                sink,
            )
        )

        assert session.config.agent.provider == "claude"
        assert session.adapter.adapter_name == "claude"
        assert session._agent_started is False

    def test_an_invalid_draft_refuses_without_starting(self) -> None:
        session, original = make_session()
        sink = Sink()

        run(
            session.handle(
                inbound(
                    "run_control",
                    {"action": "start", "config": {"provider": "not-a-provider"}},
                ),
                sink,
            )
        )

        assert sink.last("session_configured")["accepted"] is False
        assert session.adapter is original
        assert original.prompts == []


class TestReadinessChecklist:
    def test_every_blocker_has_an_action(self) -> None:
        adapter = FakeAgentAdapter()
        session = WorkbenchSession(
            WorkbenchConfig(target_url=LOCAL, mode=RunMode.AUTO), adapter
        )

        for blocker in session.readiness().blockers:
            assert blocker.action, f"{blocker.key} blocks with no action"

    def test_summary_names_a_concrete_next_step(self) -> None:
        adapter = FakeAgentAdapter()
        session = WorkbenchSession(
            WorkbenchConfig(target_url=LOCAL, mode=RunMode.AUTO), adapter
        )

        summary = session.readiness().summary()

        assert summary.startswith("Start unavailable: ")
        assert len(summary) > len("Start unavailable: ")

    def test_a_complete_configuration_is_ready(self) -> None:
        session, _ = make_session(mode=RunMode.AUTO)

        readiness = session.readiness()

        assert readiness.ready, [b.to_dict() for b in readiness.blockers]
        assert readiness.summary() == "Ready to start."

    def test_payload_only_needs_no_locators_for_a_first_payload(self) -> None:
        adapter = FakeAgentAdapter()
        session = WorkbenchSession(
            WorkbenchConfig(target_url=LOCAL, mode=RunMode.PAYLOAD_ONLY), adapter
        )

        readiness = session.readiness()

        assert readiness.ready, [b.to_dict() for b in readiness.blockers]

    def test_auto_send_confirmation_is_a_warning_not_a_blocker(self) -> None:
        session, _ = make_session(mode=RunMode.AUTO)

        checks = {c.key: c for c in session.readiness().checks}

        assert checks["auto_send"].state.value == "warn"
        assert not checks["auto_send"].blocking

    def test_missing_reply_locator_is_named_explicitly(self) -> None:
        adapter = FakeAgentAdapter()
        session = WorkbenchSession(
            WorkbenchConfig(target_url=LOCAL, mode=RunMode.SUPERVISED), adapter
        )

        actions = [c.action for c in session.readiness().blockers]

        assert any("reply element" in action for action in actions)


class TestAutoConfirmationContract:
    def test_interactive_auto_needs_no_command_line_authorization(self) -> None:
        config = build_workbench_config(
            target_url=LOCAL, mode="auto", allow_auto_send=False
        )

        assert config.auto_send_authorization_problem(interactive=True) == ""

    def test_headless_auto_requires_authorization(self) -> None:
        config = build_workbench_config(
            target_url=LOCAL, mode="auto", allow_auto_send=False
        )

        problem = config.auto_send_authorization_problem(interactive=False)

        assert "--allow-auto-send" in problem

    def test_authorized_headless_auto_is_allowed(self) -> None:
        config = build_workbench_config(
            target_url=LOCAL, mode="auto", allow_auto_send=True
        )

        assert config.auto_send_authorization_problem(interactive=False) == ""

    def test_non_auto_modes_never_need_authorization(self) -> None:
        for mode in ("payload_only", "manual", "supervised"):
            config = build_workbench_config(target_url=LOCAL, mode=mode)
            assert config.auto_send_authorization_problem(interactive=False) == ""

    def test_auto_send_is_not_a_configuration_problem(self) -> None:
        # It is a runtime confirmation, so it must not stop the dock opening.
        config = build_workbench_config(
            target_url=LOCAL, mode="auto", allow_auto_send=False
        )

        assert not any(
            "allow-auto-send" in problem for problem in config.preflight_problems()
        )

    def test_pressing_start_records_the_dock_as_the_confirmation_source(self) -> None:
        session, _ = make_session(mode=RunMode.AUTO)
        sink = Sink()

        run(session.handle(inbound("run_control", {"action": "start"}), sink))

        assert session.auto_send_confirmed_by == "dock"
        assert session.result_document()["adapter"]["auto_send_confirmed_by"] == "dock"
