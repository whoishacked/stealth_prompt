"""Session-level correlation and mode tests.

These cover the frames the extension can send and the ways they can be wrong:
from the wrong tab, for the wrong turn, twice, or out of order. Each must be
refused rather than recorded, because a transcript that attributes a reply to
the wrong payload is worse than no transcript.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from typing import Any, TypeVar

from stealth_prompt.agents import FakeAgentAdapter
from stealth_prompt.oracles import DisclosureStatus, Oracle, OracleType
from stealth_prompt.workbench.artifacts import ArtifactStore
from stealth_prompt.workbench.binding import (
    BindingStore,
    BoundLocator,
    TargetBinding,
)
from stealth_prompt.workbench.config import RunMode, SafetySettings, WorkbenchConfig
from stealth_prompt.workbench.operations import (
    LocatorStrategy,
    SubmitAction,
    SubmitStrategy,
)
from stealth_prompt.workbench.protocol import decode
from stealth_prompt.workbench.session import WorkbenchSession
from stealth_prompt.workbench.state import RunState, StopReason

T = TypeVar("T")
CANARY = "SP_CANARY_ABC123XYZ789"
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

    def last_error(self) -> dict[str, Any]:
        errors = self.of("error")
        assert errors, f"no error frame in {[f['type'] for f in self.frames]}"
        return errors[-1]["payload"]


def inbound(type_: str, payload: dict[str, Any] | None = None):
    return decode(json.dumps({"type": type_, "payload": payload or {}}), max_bytes=65536)


def a_binding() -> TargetBinding:
    return TargetBinding(
        target_origin="http://127.0.0.1:8765",
        input=BoundLocator(
            strategy=LocatorStrategy.ROLE, value="textbox", name="Message"
        ),
        submit_locator=BoundLocator(
            strategy=LocatorStrategy.ROLE, value="button", name="Send"
        ),
        submit_action=SubmitAction(strategy=SubmitStrategy.CLICK_BUTTON),
        response_locator=BoundLocator(
            strategy=LocatorStrategy.CSS, value=".assistant-message", pick="last"
        ),
    )


def make(
    *,
    mode: RunMode = RunMode.MANUAL,
    binding: TargetBinding | None = None,
    oracles: list[Oracle] | None = None,
    store: ArtifactStore | None = None,
    binding_store: BindingStore | None = None,
) -> tuple[WorkbenchSession, FakeAgentAdapter]:
    adapter = FakeAgentAdapter([["a payload"]])
    config = WorkbenchConfig(
        target_url=LOCAL, mode=mode, allow_auto_send=mode is RunMode.AUTO
    )
    session = WorkbenchSession(
        config,
        adapter,
        oracles=oracles or [],
        store=store,
        binding=binding,
        binding_store=binding_store,
    )
    return session, adapter


async def approve(session: WorkbenchSession, sink: Sink, payload: str = "p") -> None:
    await session.handle(
        inbound(
            "send_approved",
            {"approved": True, "payload": payload, "selector": "#send"},
        ),
        sink,
    )


class TestPageBinding:
    def test_first_tab_binds_the_run(self) -> None:
        session, _ = make()
        sink = Sink()

        run(session.handle(inbound("hello", {"page_id": "page-1"}), sink))

        assert session.machine.page_id == "page-1"

    def test_a_second_tab_is_refused(self) -> None:
        # Two tabs open on the target must not both drive the run.
        session, _ = make()
        sink = Sink()

        async def scenario() -> None:
            await session.handle(inbound("hello", {"page_id": "page-1"}), sink)
            await session.handle(inbound("hello", {"page_id": "page-2"}), sink)

        run(scenario())

        assert sink.last_error()["code"] == "page_conflict"

    def test_frames_from_another_tab_are_refused(self) -> None:
        session, _ = make()
        sink = Sink()

        async def scenario() -> None:
            await session.handle(inbound("hello", {"page_id": "page-1"}), sink)
            await session.handle(
                inbound("operator_prompt", {"text": "go", "page_id": "page-2"}), sink
            )

        run(scenario())

        assert sink.last_error()["code"] == "wrong_page"

    def test_one_operation_cannot_be_executed_twice(self) -> None:
        session, _ = make()
        sink = Sink()

        async def scenario() -> None:
            await session.handle(inbound("hello", {"page_id": "page-1"}), sink)
            await approve(session, sink)
            operation = session.machine.pending
            assert operation is not None
            frame = {
                "ok": True,
                "operation_id": operation.operation_id,
                "turn_id": operation.turn_id,
                "page_id": "page-1",
            }
            await session.handle(inbound("operation_result", frame), sink)
            # The same result again, as a duplicate tab would send.
            await session.handle(inbound("operation_result", frame), sink)

        run(scenario())

        assert sink.last_error()["code"] == "no_pending_operation"


class TestTurnCorrelation:
    def test_a_late_reply_from_a_previous_turn_is_refused(self) -> None:
        session, _ = make()
        sink = Sink()

        async def scenario() -> str:
            await approve(session, sink, "first")
            first_turn = session.turns[-1].turn_id
            await session.handle(
                inbound("target_response", {"text": "reply one", "turn_id": first_turn}),
                sink,
            )
            await approve(session, sink, "second")
            # Turn 1's reply arrives late, during turn 2.
            await session.handle(
                inbound("target_response", {"text": "LATE", "turn_id": first_turn}),
                sink,
            )
            return session.turns[-1].target_response

        second_response = run(scenario())

        assert second_response == ""
        assert sink.last_error()["code"] == "turn_mismatch"

    def test_a_duplicate_reply_cannot_overwrite_a_completed_turn(self) -> None:
        session, _ = make()
        sink = Sink()

        async def scenario() -> str:
            await approve(session, sink)
            turn_id = session.turns[-1].turn_id
            await session.handle(
                inbound("target_response", {"text": "the real reply", "turn_id": turn_id}),
                sink,
            )
            await session.handle(
                inbound("target_response", {"text": "OVERWRITE", "turn_id": turn_id}),
                sink,
            )
            return session.turns[-1].target_response

        assert run(scenario()) == "the real reply"
        assert sink.last_error()["code"] == "turn_already_complete"

    def test_uncorrelated_replies_still_work_for_manual_use(self) -> None:
        # Manual frames predate correlation and legitimately omit turn ids.
        session, _ = make()
        sink = Sink()

        async def scenario() -> str:
            await approve(session, sink)
            await session.handle(inbound("target_response", {"text": "hello"}), sink)
            return session.turns[-1].target_response

        assert run(scenario()) == "hello"


class TestCaptureFailure:
    def test_capture_failure_is_inconclusive_never_not_detected(self) -> None:
        oracle = Oracle(
            oracle_id="canary", oracle_type=OracleType.FRAGMENT, pattern=CANARY
        )
        session, _ = make(oracles=[oracle])
        sink = Sink()

        async def scenario() -> None:
            await approve(session, sink)
            await session.handle(
                inbound(
                    "capture_failed",
                    {"code": "capture_timeout", "elapsed_ms": 60000, "partial_text": "pa"},
                ),
                sink,
            )

        run(scenario())

        record = session.turns[-1]
        assert record.status is DisclosureStatus.INCONCLUSIVE
        assert record.status is not DisclosureStatus.NOT_DETECTED
        assert record.capture_error is not None
        assert record.capture_error["code"] == "capture_timeout"

    def test_capture_failure_is_reported_to_the_dock(self) -> None:
        session, _ = make()
        sink = Sink()

        async def scenario() -> None:
            await approve(session, sink)
            await session.handle(
                inbound("capture_failed", {"code": "capture_timeout"}), sink
            )

        run(scenario())

        assert sink.last_error()["code"] == "capture_failed"

    def test_capture_failure_records_partial_observation(self) -> None:
        session, _ = make()
        sink = Sink()

        async def scenario() -> None:
            await approve(session, sink)
            await session.handle(
                inbound("capture_failed", {"code": "cancelled", "partial_text": "half"}),
                sink,
            )

        run(scenario())

        assert session.turns[-1].capture_error["partial_observed"] is True


class TestSubmitStrategy:
    def test_click_binding_produces_a_click_operation(self) -> None:
        # A button "submitted" with Enter does nothing on a non-form page.
        session, _ = make(binding=a_binding())
        sink = Sink()

        run(approve(session, sink))

        assert sink.of("perform_operation")[-1]["payload"]["operation"] == "click"

    def test_press_binding_produces_a_press_operation(self) -> None:
        binding = a_binding()
        binding = TargetBinding(
            target_origin=binding.target_origin,
            input=binding.input,
            submit_locator=binding.submit_locator,
            submit_action=SubmitAction(strategy=SubmitStrategy.PRESS_KEY, key="Enter"),
            response_locator=binding.response_locator,
        )
        session, _ = make(binding=binding)
        sink = Sink()

        run(approve(session, sink))

        payload = sink.of("perform_operation")[-1]["payload"]
        assert payload["operation"] == "press"
        assert payload["key"] == "Enter"


class TestBindingLifecycle:
    def test_hello_announces_a_loaded_binding(self) -> None:
        session, _ = make(binding=a_binding())
        sink = Sink()

        run(session.handle(inbound("hello"), sink))

        ready = sink.of("ready")[-1]["payload"]
        assert ready["binding_loaded"] is True
        assert "reply" in ready["binding_summary"]
        assert sink.of("binding")

    def test_no_binding_is_reported_honestly(self) -> None:
        session, _ = make()
        sink = Sink()

        run(session.handle(inbound("hello"), sink))

        assert sink.of("ready")[-1]["payload"]["binding_loaded"] is False

    def test_saving_a_binding_persists_and_loads_it(self, tmp_path) -> None:
        store = BindingStore(tmp_path)
        session, _ = make(binding_store=store)
        sink = Sink()

        async def scenario() -> None:
            await session.handle(
                inbound(
                    "save_binding",
                    {
                        "binding": {
                            "input": {"strategy": "css", "value": "#message"},
                            "submit": {
                                "strategy": "click_button",
                                "locator": {"strategy": "css", "value": "#send"},
                            },
                            "response": {
                                "locator": {"strategy": "css", "value": ".reply"},
                                "pick": "last",
                            },
                        }
                    },
                ),
                sink,
            )

        run(scenario())

        assert sink.of("binding")[-1]["payload"]["saved"] is True
        assert store.load(LOCAL) is not None
        assert session.machine.state is RunState.READY

    def test_an_invalid_binding_is_refused(self, tmp_path) -> None:
        session, _ = make(binding_store=BindingStore(tmp_path))
        sink = Sink()

        run(
            session.handle(
                inbound("save_binding", {"binding": {"input": {"strategy": "xpath"}}}),
                sink,
            )
        )

        assert sink.last_error()["code"] == "invalid_binding"


class TestRunControl:
    def test_stop_records_the_reason(self) -> None:
        session, _ = make()
        sink = Sink()

        run(session.handle(inbound("run_control", {"action": "stop"}), sink))

        assert session.stop_reason is StopReason.OPERATOR_STOP

    def test_manual_start_authors_the_first_payload(self) -> None:
        # Start in manual mode no longer errors: it means "author the first
        # payload from the objective", so the operator never has to invent an
        # instruction to get going.
        session, adapter = make(mode=RunMode.MANUAL, binding=a_binding())
        sink = Sink()

        run(session.handle(inbound("run_control", {"action": "start"}), sink))

        assert adapter.prompts, "no planner call was made"
        assert session.pending_payload

    def test_start_without_a_binding_reports_an_actionable_reason(self) -> None:
        session, _ = make(mode=RunMode.SUPERVISED)
        sink = Sink()

        run(session.handle(inbound("run_control", {"action": "start"}), sink))

        error = sink.last_error()
        assert error["code"] == "not_ready"
        # The summary names a concrete next step, not "invalid state".
        assert error["message"].startswith("Start unavailable: ")
        plan = sink.of("run_plan")[-1]["payload"]["readiness"]
        keys = {item["key"] for item in plan["blockers"]}
        assert "binding" in keys

    def test_refused_start_returns_the_full_checklist(self) -> None:
        session, _ = make(mode=RunMode.SUPERVISED)
        sink = Sink()

        run(session.handle(inbound("run_control", {"action": "start"}), sink))

        plan = sink.of("run_plan")[-1]["payload"]
        assert plan["event"] == "start_refused"
        blockers = plan["readiness"]["blockers"]
        assert blockers
        # Every blocker carries an action the operator can take.
        assert all(item["action"] for item in blockers)

    def test_unknown_action_is_refused(self) -> None:
        session, _ = make()
        sink = Sink()

        run(session.handle(inbound("run_control", {"action": "evaluate"}), sink))

        assert sink.last_error()["code"] == "unknown_action"


class TestResultDocument:
    def test_records_mode_stop_reason_and_usage(self, tmp_path) -> None:
        store = ArtifactStore(tmp_path, session_id="s-1")
        session, _ = make(binding=a_binding(), store=store)
        sink = Sink()

        async def scenario() -> dict[str, Any]:
            await approve(session, sink)
            await session.handle(inbound("target_response", {"text": "hi"}), sink)
            return await session.finalize()

        document = run(scenario())

        json.dumps(document)
        assert document["schema_version"] == 2
        assert document["mode"] == "manual"
        assert document["binding"]["target_origin"] == "http://127.0.0.1:8765"
        assert "usage" in document
        assert "state_transitions" in document
        assert document["target_data_sharing"] == "none"

    def test_transcript_can_be_withheld_while_digests_remain(self, tmp_path) -> None:
        store = ArtifactStore(tmp_path, session_id="s-2")
        adapter = FakeAgentAdapter()
        config = WorkbenchConfig(
            target_url=LOCAL, safety=SafetySettings(store_transcript=False)
        )
        session = WorkbenchSession(config, adapter, store=store)
        sink = Sink()

        async def scenario() -> dict[str, Any]:
            await approve(session, sink, "secret payload")
            await session.handle(
                inbound("target_response", {"text": "secret reply"}), sink
            )
            return await session.finalize()

        document = run(scenario())

        text = json.dumps(document)
        assert "secret payload" not in text
        assert "secret reply" not in text

    def test_integrity_failure_is_never_not_detected(self, tmp_path) -> None:
        oracle = Oracle(
            oracle_id="canary", oracle_type=OracleType.FRAGMENT, pattern=CANARY
        )
        store = ArtifactStore(tmp_path, session_id="s-3")
        session, _ = make(oracles=[oracle], store=store)
        session.stop_reason = StopReason.CAPTURE_TIMEOUT
        sink = Sink()

        async def scenario() -> dict[str, Any]:
            await approve(session, sink)
            await session.handle(inbound("target_response", {"text": "bland"}), sink)
            return await session.finalize()

        document = run(scenario())

        assert document["status"] == "error"
        assert document["stop_reason"] == "capture_timeout"
