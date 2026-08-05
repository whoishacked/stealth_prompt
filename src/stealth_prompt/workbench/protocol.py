"""Wire protocol between the browser extension and the Python broker.

The extension runs inside a page that the target application controls, so every
frame arriving here is treated as hostile input: unknown types are refused,
sizes are capped, and each field is validated to a concrete type before use.
Decoding never evaluates anything.

The message set is closed. In particular there is no ``run``, ``eval``, or
``navigate`` inbound message: the only browser work the broker will accept is
one of the six allowlisted operations in :mod:`~stealth_prompt.workbench.operations`,
and the only outbound instruction is an operation the *operator* triggered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .operations import BrowserOperation, parse_key, parse_operation

PROTOCOL_VERSION = 1


class MessageType(str, Enum):
    """Every frame the broker will send or accept."""

    # Extension -> broker
    HELLO = "hello"
    OPERATOR_PROMPT = "operator_prompt"
    OPERATOR_INTERRUPT = "operator_interrupt"
    OPERATION_RESULT = "operation_result"
    TARGET_RESPONSE = "target_response"
    SEND_APPROVED = "send_approved"
    CAPTURE_FAILED = "capture_failed"
    SAVE_BINDING = "save_binding"
    RUN_CONTROL = "run_control"
    CAPABILITIES_REQUEST = "capabilities_request"
    CONFIGURE_SESSION = "configure_session"
    PROVIDER_HEALTH_REQUEST = "provider_health_request"
    MODEL_LIST_REQUEST = "model_list_request"
    PING = "ping"

    # Broker -> extension
    READY = "ready"
    AGENT_EVENT = "agent_event"
    PERFORM_OPERATION = "perform_operation"
    STATUS = "status"
    RUN_STATE = "run_state"
    BINDING = "binding"
    CAPABILITIES = "capabilities"
    SESSION_CONFIGURED = "session_configured"
    PROVIDER_HEALTH = "provider_health"
    MODEL_LIST = "model_list"
    RUN_PLAN = "run_plan"
    ERROR = "error"
    PONG = "pong"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


INBOUND_TYPES = frozenset(
    {
        MessageType.HELLO,
        MessageType.OPERATOR_PROMPT,
        MessageType.OPERATOR_INTERRUPT,
        MessageType.OPERATION_RESULT,
        MessageType.TARGET_RESPONSE,
        MessageType.SEND_APPROVED,
        MessageType.CAPTURE_FAILED,
        MessageType.SAVE_BINDING,
        MessageType.RUN_CONTROL,
        MessageType.CAPABILITIES_REQUEST,
        MessageType.CONFIGURE_SESSION,
        MessageType.PROVIDER_HEALTH_REQUEST,
        MessageType.MODEL_LIST_REQUEST,
        MessageType.PING,
    }
)

OUTBOUND_TYPES = frozenset(
    {
        MessageType.READY,
        MessageType.AGENT_EVENT,
        MessageType.PERFORM_OPERATION,
        MessageType.STATUS,
        MessageType.RUN_STATE,
        MessageType.BINDING,
        MessageType.CAPABILITIES,
        MessageType.SESSION_CONFIGURED,
        MessageType.PROVIDER_HEALTH,
        MessageType.MODEL_LIST,
        MessageType.RUN_PLAN,
        MessageType.ERROR,
        MessageType.PONG,
    }
)


class ProtocolError(ValueError):
    """A frame was malformed, oversized, or of an unknown type."""


@dataclass(frozen=True)
class InboundMessage:
    """A validated frame from the extension."""

    type: MessageType
    payload: dict[str, Any] = field(default_factory=dict)

    def text(self, key: str, *, max_bytes: int, required: bool = True) -> str:
        """Read a string field, enforcing type and size."""
        value = self.payload.get(key)
        if value is None:
            if required:
                raise ProtocolError(f"{self.type.value} is missing required field {key!r}")
            return ""
        if not isinstance(value, str):
            raise ProtocolError(f"{self.type.value} field {key!r} must be a string")
        size = len(value.encode("utf-8"))
        if size > max_bytes:
            raise ProtocolError(
                f"{self.type.value} field {key!r} is {size} bytes, above the "
                f"{max_bytes}-byte limit"
            )
        return value

    def integer(self, key: str, *, default: int = 0) -> int:
        value = self.payload.get(key, default)
        # bool is an int subclass; refuse it so True never becomes turn 1.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError(f"{self.type.value} field {key!r} must be an integer")
        return value

    def correlation(self, key: str) -> str:
        """Read a correlation id. Absent is allowed; a wrong type is not.

        Manual-mode frames predate correlation and legitimately omit these, so
        an empty string means "uncorrelated" rather than "invalid".
        """
        value = self.payload.get(key, "")
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ProtocolError(f"{self.type.value} field {key!r} must be a string")
        if len(value) > 128:
            raise ProtocolError(f"{self.type.value} field {key!r} is too long")
        return value

    def boolean(self, key: str, *, default: bool = False) -> bool:
        value = self.payload.get(key, default)
        if not isinstance(value, bool):
            raise ProtocolError(f"{self.type.value} field {key!r} must be a boolean")
        return value


def decode(raw: str | bytes, *, max_bytes: int) -> InboundMessage:
    """Decode and validate one inbound frame.

    Raises:
        ProtocolError: the frame is oversized, not JSON, not an object, or of a
            type the broker does not accept from the extension.
    """
    if isinstance(raw, str):
        encoded = raw.encode("utf-8")
    else:
        encoded = raw
    if len(encoded) > max_bytes:
        raise ProtocolError(
            f"frame is {len(encoded)} bytes, above the {max_bytes}-byte limit"
        )

    try:
        parsed = json.loads(encoded)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"frame is not valid JSON: {exc.__class__.__name__}") from None

    if not isinstance(parsed, dict):
        raise ProtocolError("frame must be a JSON object")

    raw_type = parsed.get("type")
    if not isinstance(raw_type, str):
        raise ProtocolError("frame is missing a string 'type'")

    try:
        message_type = MessageType(raw_type)
    except ValueError:
        raise ProtocolError(f"unknown message type {raw_type!r}") from None

    if message_type not in INBOUND_TYPES:
        raise ProtocolError(
            f"message type {raw_type!r} is not accepted from the extension"
        )

    payload = parsed.get("payload", {})
    if not isinstance(payload, dict):
        raise ProtocolError("'payload' must be a JSON object")

    return InboundMessage(type=message_type, payload=payload)


def encode(
    message_type: MessageType,
    payload: dict[str, Any] | None = None,
    *,
    envelope: dict[str, Any] | None = None,
) -> str:
    """Encode one outbound frame.

    ``envelope`` carries the correlation fields (run, page, turn, operation) so
    the extension can echo them back and the broker can reject anything stale.
    """
    if message_type not in OUTBOUND_TYPES:
        raise ProtocolError(f"message type {message_type} is not sent by the broker")
    frame: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "type": message_type.value,
        "payload": payload or {},
    }
    if envelope:
        frame.update(envelope)
    return json.dumps(frame, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class OperationRequest:
    """An allowlisted browser operation, built only from operator intent."""

    operation: BrowserOperation
    selector: str = ""
    value: str = ""
    key: str = ""
    turn: int = 0
    target: str = "input"
    stable_ms: int = 0
    timeout_ms: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "operation": self.operation.value,
            "selector": self.selector,
            "value": self.value,
            "key": self.key,
            "turn": self.turn,
            # Which bound element the operation applies to. The extension never
            # chooses; it is told.
            "target": self.target,
            "stable_ms": self.stable_ms,
            "timeout_ms": self.timeout_ms,
        }


def build_operation(
    name: str,
    *,
    selector: str = "",
    value: str = "",
    key: str = "",
    turn: int = 0,
    target: str = "input",
    stable_ms: int = 0,
    timeout_ms: int = 0,
) -> OperationRequest:
    """Build an operation request, refusing anything off the allowlist.

    This is the only constructor the broker uses, so an operation name that is
    not one of the six verbs cannot reach the extension.
    """
    operation = parse_operation(name)
    if operation is BrowserOperation.PRESS:
        key = parse_key(key)
    if operation in {
        BrowserOperation.FILL,
        BrowserOperation.CLICK,
        BrowserOperation.PRESS,
        BrowserOperation.WAIT_FOR,
        BrowserOperation.EXTRACT,
    } and not selector.strip():
        raise ValueError(f"operation {operation.value!r} requires a selector")
    return OperationRequest(
        operation=operation,
        selector=selector,
        value=value,
        key=key,
        turn=turn,
        target=target,
        stable_ms=stable_ms,
        timeout_ms=timeout_ms,
    )
