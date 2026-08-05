"""A versioned, append-only record of what happened in a session.

This exists now, before there is much to record, because the alternative is a
data model that cannot describe anything except the current feature. Later work
(navigation, request observation, auth boundaries) can add event kinds without
reshaping what is already written or invalidating exported evidence.

Events are sanitized on the way in. Nothing here should ever hold a credential,
a cookie, or an unbounded target response -- large or sensitive content lives in
the artifact store and is referenced by id.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..workbench.artifacts import utc_now
from ..workbench.redaction import bound, sanitize_for_terminal

TIMELINE_SCHEMA_VERSION = 1

#: Ceiling on any single string stored in an event.
MAX_FIELD_CHARS = 2000
MAX_EVENTS = 5000


class EventKind(str, Enum):
    """Everything the MVP records.

    Deliberately small. Future kinds -- ``navigation.observed``,
    ``request.observed``, ``response.observed``, ``auth.boundary_detected`` --
    are documented in the architecture notes but not implemented, because
    shipping unused machinery is how a plugin framework grows by accident.
    """

    SESSION_STARTED = "session.started"
    INTERACTION_BOUND = "interaction.bound"
    CONVERSATION_CAPTURED = "conversation.captured"
    PROPOSAL_GENERATED = "proposal.generated"
    PROPOSAL_EDITED = "proposal.edited"
    PROPOSAL_APPROVED = "proposal.approved"
    PROPOSAL_REFUSED = "proposal.refused"
    PAYLOAD_SENT = "payload.sent"
    RESPONSE_CAPTURED = "response.captured"
    EVALUATION_COMPLETED = "evaluation.completed"
    SESSION_STOPPED = "session.stopped"
    ERROR = "error"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class EventSource(str, Enum):
    """Who caused an event. Useful when reading a transcript months later."""

    OPERATOR = "operator"
    CORE = "core"
    PROVIDER = "provider"
    BROWSER = "browser"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


def new_event_id() -> str:
    return f"evt-{secrets.token_hex(8)}"


def _sanitize(value: Any) -> Any:
    """Make one metadata value safe to store and later print."""
    if isinstance(value, str):
        cleaned, _ = bound(sanitize_for_terminal(value, limit=MAX_FIELD_CHARS),
                           max_bytes=MAX_FIELD_CHARS * 4)
        return cleaned
    if isinstance(value, dict):
        return {str(k)[:120]: _sanitize(v) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in list(value)[:40]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_FIELD_CHARS]


@dataclass(frozen=True)
class TimelineEvent:
    """One thing that happened."""

    event_id: str
    kind: EventKind
    source: EventSource
    at: str
    session_id: str = ""
    turn_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "source": self.source.value,
            "at": self.at,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "metadata": self.metadata,
        }


@dataclass
class Timeline:
    """An append-only, bounded event log for one session."""

    session_id: str = ""
    schema_version: int = TIMELINE_SCHEMA_VERSION
    events: list[TimelineEvent] = field(default_factory=list)

    def record(
        self,
        kind: EventKind,
        *,
        source: EventSource = EventSource.CORE,
        turn_id: str = "",
        **metadata: Any,
    ) -> TimelineEvent:
        """Append one sanitized event and return it."""
        event = TimelineEvent(
            event_id=new_event_id(),
            kind=kind,
            source=source,
            at=utc_now().isoformat(),
            session_id=self.session_id,
            turn_id=turn_id,
            metadata={key: _sanitize(value) for key, value in metadata.items()},
        )
        self.events.append(event)
        if len(self.events) > MAX_EVENTS:
            # Drop from the front rather than growing without bound. The
            # artifact store holds the authoritative full record.
            del self.events[: len(self.events) - MAX_EVENTS]
        return event

    def of_kind(self, kind: EventKind) -> list[TimelineEvent]:
        return [event for event in self.events if event.kind is kind]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "events": [event.to_dict() for event in self.events],
        }
