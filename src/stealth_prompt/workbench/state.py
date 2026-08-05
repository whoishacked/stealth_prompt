"""The run state machine and its correlation rules.

State lives in Python, never in the extension. The extension reports what
happened; this module decides what that means and what may happen next. That
split matters because the extension runs inside a page the target controls: if
the page could drive state transitions, it could drive the test.

Correlation is the other half. Every command carries a ``run_id``, ``turn_id``,
and ``operation_id``, and results are matched against the operation actually
being awaited. Without that, a slow reply from turn 3 arriving during turn 4 is
indistinguishable from turn 4's own reply -- and would be recorded as evidence
against the wrong payload.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import Enum


class RunState(str, Enum):
    """Where a run currently is."""

    SETUP = "setup"
    READY = "ready"
    PLANNING = "planning"
    PAYLOAD_READY = "payload_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    SENDING = "sending"
    WAITING_FOR_RESPONSE = "waiting_for_response"
    EVALUATING = "evaluating"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class StopReason(str, Enum):
    """Why a run ended. Recorded verbatim in the result."""

    CONFIRMED = "confirmed_evidence"
    PLANNER_STOP = "planner_stop"
    OPERATOR_STOP = "operator_stop"
    MAX_TURNS = "max_turns"
    MAX_DURATION = "max_duration"
    COST_LIMIT = "cost_limit"
    NO_PROGRESS_PAYLOAD = "repeated_payloads"
    NO_PROGRESS_RESPONSE = "repeated_responses"
    CONSECUTIVE_REFUSALS = "consecutive_refusals"
    CAPTURE_TIMEOUT = "capture_timeout"
    TARGET_UNAVAILABLE = "target_unavailable"
    AGENT_UNAVAILABLE = "agent_unavailable"
    #: The backend ran fine but declined to author a payload.
    AGENT_REFUSED = "agent_refused"
    RATE_LIMITED = "rate_limited"
    PROTOCOL_ERROR = "protocol_error"
    COMPLETED = "completed"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Reasons that mean the run could not establish a trustworthy result. A run
#: ending for one of these must never be reported as "not detected".
INTEGRITY_FAILURE_REASONS = frozenset(
    {
        StopReason.CAPTURE_TIMEOUT,
        StopReason.TARGET_UNAVAILABLE,
        StopReason.AGENT_UNAVAILABLE,
        StopReason.AGENT_REFUSED,
        StopReason.PROTOCOL_ERROR,
    }
)


#: Legal transitions. Anything absent raises, which turns a logic slip into a
#: loud typed failure rather than a silently wrong transcript.
_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.SETUP: frozenset({RunState.READY, RunState.STOPPING, RunState.ERROR}),
    RunState.READY: frozenset(
        {RunState.PLANNING, RunState.SETUP, RunState.STOPPING, RunState.ERROR}
    ),
    RunState.PLANNING: frozenset(
        {RunState.PAYLOAD_READY, RunState.STOPPING, RunState.ERROR}
    ),
    RunState.PAYLOAD_READY: frozenset(
        {
            RunState.AWAITING_APPROVAL,
            RunState.SENDING,
            RunState.PLANNING,
            RunState.STOPPING,
            RunState.ERROR,
        }
    ),
    RunState.AWAITING_APPROVAL: frozenset(
        {RunState.SENDING, RunState.PLANNING, RunState.STOPPING, RunState.ERROR}
    ),
    RunState.SENDING: frozenset(
        {RunState.WAITING_FOR_RESPONSE, RunState.STOPPING, RunState.ERROR}
    ),
    RunState.WAITING_FOR_RESPONSE: frozenset(
        {RunState.EVALUATING, RunState.STOPPING, RunState.ERROR}
    ),
    RunState.EVALUATING: frozenset(
        {RunState.READY, RunState.PLANNING, RunState.STOPPING, RunState.ERROR}
    ),
    RunState.STOPPING: frozenset({RunState.STOPPED, RunState.ERROR}),
    RunState.STOPPED: frozenset(),
    RunState.ERROR: frozenset({RunState.STOPPING, RunState.STOPPED}),
}

TERMINAL_STATES = frozenset({RunState.STOPPED})


class StateError(RuntimeError):
    """An illegal transition or a correlation mismatch."""

    def __init__(self, message: str, *, code: str = "illegal_state") -> None:
        super().__init__(message)
        self.code = code


def new_id(prefix: str) -> str:
    """An unguessable, readable correlation id."""
    return f"{prefix}-{secrets.token_hex(6)}"


@dataclass
class PendingOperation:
    """The one browser operation a turn is currently awaiting."""

    operation_id: str
    turn_id: str
    kind: str


@dataclass
class RunStateMachine:
    """Tracks state and correlation for one run.

    One run owns one page. One turn owns at most one in-flight operation and at
    most one capture.
    """

    run_id: str
    state: RunState = RunState.SETUP
    page_id: str = ""
    turn_id: str = ""
    turn_number: int = 0
    pending: PendingOperation | None = None
    capture_id: str = ""
    stop_reason: StopReason | None = None
    completed_turns: set[str] = None  # type: ignore[assignment]
    history: list[tuple[RunState, RunState]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.completed_turns is None:
            self.completed_turns = set()
        if self.history is None:
            self.history = []

    # ------------------------------------------------------------ transitions

    def can_transition(self, target: RunState) -> bool:
        return target in _TRANSITIONS.get(self.state, frozenset())

    def transition(self, target: RunState) -> None:
        """Move to ``target`` or raise :class:`StateError`."""
        if target is self.state:
            return
        if not self.can_transition(target):
            raise StateError(
                f"illegal transition {self.state.value} -> {target.value}",
                code="illegal_transition",
            )
        self.history.append((self.state, target))
        self.state = target

    def fail(self, reason: StopReason) -> None:
        """Move to ERROR, recording why."""
        self.stop_reason = reason
        if self.state is not RunState.ERROR:
            self.history.append((self.state, RunState.ERROR))
            self.state = RunState.ERROR

    def stop(self, reason: StopReason) -> None:
        """Begin an orderly stop."""
        if self.stop_reason is None:
            self.stop_reason = reason
        if self.state in TERMINAL_STATES:
            return
        if self.state is not RunState.STOPPING:
            self.history.append((self.state, RunState.STOPPING))
            self.state = RunState.STOPPING

    def finish(self) -> None:
        if self.state is RunState.STOPPED:
            return
        self.history.append((self.state, RunState.STOPPED))
        self.state = RunState.STOPPED

    # ------------------------------------------------------------ correlation

    def bind_page(self, page_id: str) -> None:
        """Bind the run to one page. A second, different page is refused."""
        if self.page_id and page_id != self.page_id:
            raise StateError(
                "another page is already bound to this run", code="page_conflict"
            )
        self.page_id = page_id

    def check_page(self, page_id: str) -> None:
        if not page_id:
            return
        if self.page_id and page_id != self.page_id:
            raise StateError(
                "frame came from a page that is not bound to this run",
                code="wrong_page",
            )

    def begin_turn(self) -> str:
        self.turn_number += 1
        self.turn_id = new_id("turn")
        self.pending = None
        self.capture_id = ""
        return self.turn_id

    def begin_operation(self, kind: str) -> str:
        if self.pending is not None:
            raise StateError(
                f"turn already has an in-flight {self.pending.kind} operation",
                code="operation_in_flight",
            )
        operation_id = new_id("op")
        self.pending = PendingOperation(
            operation_id=operation_id, turn_id=self.turn_id, kind=kind
        )
        return operation_id

    def complete_operation(self, operation_id: str, turn_id: str) -> PendingOperation:
        """Match a result to the operation actually being awaited."""
        pending = self.pending
        if pending is None:
            raise StateError(
                "no operation is in flight", code="no_pending_operation"
            )
        if operation_id != pending.operation_id:
            raise StateError(
                "operation_result does not match the awaited operation",
                code="operation_mismatch",
            )
        if turn_id and turn_id != pending.turn_id:
            raise StateError(
                "operation_result belongs to a different turn", code="turn_mismatch"
            )
        self.pending = None
        return pending

    def begin_capture(self) -> str:
        self.capture_id = new_id("cap")
        return self.capture_id

    def check_capture(self, capture_id: str, turn_id: str) -> None:
        """Reject a stale, duplicate, or cross-turn capture result."""
        if turn_id and turn_id in self.completed_turns:
            raise StateError(
                "a response arrived for a turn that is already complete",
                code="turn_already_complete",
            )
        if turn_id and turn_id != self.turn_id:
            raise StateError(
                "response belongs to a different turn", code="turn_mismatch"
            )
        if not self.capture_id:
            raise StateError("no capture is active", code="no_active_capture")
        if capture_id and capture_id != self.capture_id:
            raise StateError(
                "response does not match the active capture", code="capture_mismatch"
            )

    def complete_capture(self) -> None:
        if self.turn_id:
            self.completed_turns.add(self.turn_id)
        self.capture_id = ""

    def envelope(self, operation_id: str = "") -> dict[str, str | int]:
        """Correlation fields attached to every outbound command."""
        return {
            "run_id": self.run_id,
            "page_id": self.page_id,
            "turn_id": self.turn_id,
            "turn_number": self.turn_number,
            "operation_id": operation_id,
            "capture_id": self.capture_id,
            "state": self.state.value,
        }
