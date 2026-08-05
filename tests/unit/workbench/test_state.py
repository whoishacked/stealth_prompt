"""Tests for the run state machine and its correlation rules.

These encode the invariants that keep a transcript honest: one run owns one
page, one turn owns one operation, and a result that arrives late or from
somewhere else is refused rather than recorded.
"""

from __future__ import annotations

import pytest

from stealth_prompt.workbench.state import (
    INTEGRITY_FAILURE_REASONS,
    RunState,
    RunStateMachine,
    StateError,
    StopReason,
    new_id,
)


def machine() -> RunStateMachine:
    return RunStateMachine(run_id="run-test")


class TestTransitions:
    def test_starts_in_setup(self) -> None:
        assert machine().state is RunState.SETUP

    def test_happy_path(self) -> None:
        m = machine()

        for target in (
            RunState.READY,
            RunState.PLANNING,
            RunState.PAYLOAD_READY,
            RunState.SENDING,
            RunState.WAITING_FOR_RESPONSE,
            RunState.EVALUATING,
            RunState.READY,
        ):
            m.transition(target)

        assert m.state is RunState.READY
        assert len(m.history) == 7

    def test_supervised_path_goes_through_approval(self) -> None:
        m = machine()
        m.transition(RunState.READY)
        m.transition(RunState.PLANNING)
        m.transition(RunState.PAYLOAD_READY)
        m.transition(RunState.AWAITING_APPROVAL)
        m.transition(RunState.SENDING)

        assert m.state is RunState.SENDING

    @pytest.mark.parametrize(
        ("start", "target"),
        [
            (RunState.SETUP, RunState.SENDING),
            (RunState.READY, RunState.WAITING_FOR_RESPONSE),
            (RunState.PLANNING, RunState.SENDING),
            (RunState.SENDING, RunState.EVALUATING),
            (RunState.STOPPED, RunState.PLANNING),
        ],
    )
    def test_illegal_transitions_raise(
        self, start: RunState, target: RunState
    ) -> None:
        m = machine()
        m.state = start

        with pytest.raises(StateError) as excinfo:
            m.transition(target)

        assert excinfo.value.code == "illegal_transition"

    def test_transition_to_the_same_state_is_a_noop(self) -> None:
        m = machine()

        m.transition(RunState.SETUP)

        assert m.history == []

    def test_stop_then_finish(self) -> None:
        m = machine()
        m.transition(RunState.READY)
        m.stop(StopReason.OPERATOR_STOP)
        m.finish()

        assert m.state is RunState.STOPPED
        assert m.stop_reason is StopReason.OPERATOR_STOP

    def test_first_stop_reason_wins(self) -> None:
        m = machine()
        m.stop(StopReason.CONFIRMED)
        m.stop(StopReason.MAX_TURNS)

        assert m.stop_reason is StopReason.CONFIRMED

    def test_fail_records_the_reason(self) -> None:
        m = machine()
        m.fail(StopReason.CAPTURE_TIMEOUT)

        assert m.state is RunState.ERROR
        assert m.stop_reason is StopReason.CAPTURE_TIMEOUT


class TestPageBinding:
    def test_first_page_binds(self) -> None:
        m = machine()
        m.bind_page("page-1")

        assert m.page_id == "page-1"

    def test_rebinding_the_same_page_is_fine(self) -> None:
        m = machine()
        m.bind_page("page-1")
        m.bind_page("page-1")

        assert m.page_id == "page-1"

    def test_a_second_page_is_refused(self) -> None:
        # Two tabs open on the target must not both drive the run.
        m = machine()
        m.bind_page("page-1")

        with pytest.raises(StateError) as excinfo:
            m.bind_page("page-2")

        assert excinfo.value.code == "page_conflict"

    def test_frames_from_another_page_are_refused(self) -> None:
        m = machine()
        m.bind_page("page-1")

        with pytest.raises(StateError) as excinfo:
            m.check_page("page-2")

        assert excinfo.value.code == "wrong_page"

    def test_uncorrelated_frames_are_allowed(self) -> None:
        m = machine()
        m.bind_page("page-1")

        m.check_page("")


class TestOperationCorrelation:
    def test_one_operation_at_a_time(self) -> None:
        m = machine()
        m.begin_turn()
        m.begin_operation("fill")

        with pytest.raises(StateError) as excinfo:
            m.begin_operation("click")

        assert excinfo.value.code == "operation_in_flight"

    def test_matching_result_completes(self) -> None:
        m = machine()
        turn = m.begin_turn()
        operation = m.begin_operation("fill")

        completed = m.complete_operation(operation, turn)

        assert completed.kind == "fill"
        assert m.pending is None

    def test_mismatched_operation_id_refused(self) -> None:
        m = machine()
        turn = m.begin_turn()
        m.begin_operation("fill")

        with pytest.raises(StateError) as excinfo:
            m.complete_operation("op-somethingelse", turn)

        assert excinfo.value.code == "operation_mismatch"

    def test_mismatched_turn_id_refused(self) -> None:
        m = machine()
        m.begin_turn()
        operation = m.begin_operation("fill")

        with pytest.raises(StateError) as excinfo:
            m.complete_operation(operation, "turn-other")

        assert excinfo.value.code == "turn_mismatch"

    def test_result_with_nothing_pending_refused(self) -> None:
        m = machine()

        with pytest.raises(StateError) as excinfo:
            m.complete_operation("op-1", "turn-1")

        assert excinfo.value.code == "no_pending_operation"

    def test_a_second_result_for_the_same_operation_is_refused(self) -> None:
        # This is what stops two tabs from executing one operation twice.
        m = machine()
        turn = m.begin_turn()
        operation = m.begin_operation("click")
        m.complete_operation(operation, turn)

        with pytest.raises(StateError) as excinfo:
            m.complete_operation(operation, turn)

        assert excinfo.value.code == "no_pending_operation"


class TestCaptureCorrelation:
    def test_matching_capture_is_accepted(self) -> None:
        m = machine()
        turn = m.begin_turn()
        capture = m.begin_capture()

        m.check_capture(capture, turn)

    def test_late_response_from_a_previous_turn_is_refused(self) -> None:
        m = machine()
        first = m.begin_turn()
        m.begin_capture()
        m.complete_capture()
        second = m.begin_turn()
        m.begin_capture()

        with pytest.raises(StateError) as excinfo:
            m.check_capture("", first)

        assert excinfo.value.code == "turn_already_complete"
        assert second != first

    def test_duplicate_response_for_a_completed_turn_is_refused(self) -> None:
        m = machine()
        turn = m.begin_turn()
        capture = m.begin_capture()
        m.check_capture(capture, turn)
        m.complete_capture()

        with pytest.raises(StateError) as excinfo:
            m.check_capture(capture, turn)

        assert excinfo.value.code == "turn_already_complete"

    def test_mismatched_capture_id_refused(self) -> None:
        m = machine()
        turn = m.begin_turn()
        m.begin_capture()

        with pytest.raises(StateError) as excinfo:
            m.check_capture("cap-other", turn)

        assert excinfo.value.code == "capture_mismatch"

    def test_response_with_no_active_capture_refused(self) -> None:
        m = machine()
        turn = m.begin_turn()

        with pytest.raises(StateError) as excinfo:
            m.check_capture("cap-1", turn)

        assert excinfo.value.code == "no_active_capture"

    def test_beginning_a_turn_clears_the_previous_capture(self) -> None:
        m = machine()
        m.begin_turn()
        m.begin_capture()
        m.begin_turn()

        assert m.capture_id == ""
        assert m.pending is None


class TestEnvelope:
    def test_carries_every_correlation_field(self) -> None:
        m = machine()
        m.bind_page("page-1")
        m.begin_turn()
        operation = m.begin_operation("fill")

        envelope = m.envelope(operation)

        for key in ("run_id", "page_id", "turn_id", "operation_id", "state"):
            assert key in envelope
        assert envelope["operation_id"] == operation


class TestIds:
    def test_ids_are_prefixed_and_unguessable(self) -> None:
        first, second = new_id("turn"), new_id("turn")

        assert first.startswith("turn-")
        assert first != second
        assert len(first) > 8


class TestIntegrityReasons:
    @pytest.mark.parametrize(
        "reason",
        [
            StopReason.CAPTURE_TIMEOUT,
            StopReason.TARGET_UNAVAILABLE,
            StopReason.AGENT_UNAVAILABLE,
            StopReason.PROTOCOL_ERROR,
        ],
    )
    def test_failures_that_invalidate_a_negative_result(
        self, reason: StopReason
    ) -> None:
        # None of these may be reported as "not detected": the run did not
        # establish the absence of a disclosure.
        assert reason in INTEGRITY_FAILURE_REASONS

    @pytest.mark.parametrize(
        "reason", [StopReason.MAX_TURNS, StopReason.CONFIRMED, StopReason.PLANNER_STOP]
    )
    def test_ordinary_reasons_are_not_integrity_failures(
        self, reason: StopReason
    ) -> None:
        assert reason not in INTEGRITY_FAILURE_REASONS
