"""Tests for the extension/broker wire protocol.

Frames arrive from a content script running inside a page the *target* controls,
so decoding is treated as parsing hostile input.
"""

from __future__ import annotations

import json

import pytest

from stealth_prompt.workbench.protocol import (
    INBOUND_TYPES,
    OUTBOUND_TYPES,
    MessageType,
    ProtocolError,
    build_operation,
    decode,
    encode,
)

BIG = 4096


def frame(type_: str, payload: dict | None = None) -> str:
    return json.dumps({"type": type_, "payload": payload or {}})


class TestDecode:
    def test_valid_frame(self) -> None:
        message = decode(frame("operator_prompt", {"text": "hi"}), max_bytes=BIG)

        assert message.type is MessageType.OPERATOR_PROMPT
        assert message.payload["text"] == "hi"

    def test_oversized_frame_refused_before_parsing(self) -> None:
        with pytest.raises(ProtocolError, match="above the"):
            decode(frame("ping", {"pad": "x" * 5000}), max_bytes=100)

    @pytest.mark.parametrize("raw", ["not json", "{", "", "[1,2]", '"a string"', "123"])
    def test_malformed_frames_refused(self, raw: str) -> None:
        with pytest.raises(ProtocolError):
            decode(raw, max_bytes=BIG)

    def test_missing_type_refused(self) -> None:
        with pytest.raises(ProtocolError, match="missing a string 'type'"):
            decode(json.dumps({"payload": {}}), max_bytes=BIG)

    def test_unknown_type_refused(self) -> None:
        with pytest.raises(ProtocolError, match="unknown message type"):
            decode(frame("run_shell_command"), max_bytes=BIG)

    @pytest.mark.parametrize(
        "type_", ["agent_event", "perform_operation", "ready", "status", "error"]
    )
    def test_outbound_types_are_not_accepted_inbound(self, type_: str) -> None:
        # The extension must not be able to forge a frame that only the broker
        # is supposed to originate.
        with pytest.raises(ProtocolError, match="not accepted from the extension"):
            decode(frame(type_), max_bytes=BIG)

    def test_non_object_payload_refused(self) -> None:
        with pytest.raises(ProtocolError, match="'payload' must be"):
            decode(json.dumps({"type": "ping", "payload": [1]}), max_bytes=BIG)

    def test_invalid_utf8_refused(self) -> None:
        with pytest.raises(ProtocolError, match="not valid JSON"):
            decode(b"\xff\xfe{}", max_bytes=BIG)


class TestFieldAccess:
    def test_text_enforces_type(self) -> None:
        message = decode(frame("operator_prompt", {"text": 5}), max_bytes=BIG)

        with pytest.raises(ProtocolError, match="must be a string"):
            message.text("text", max_bytes=BIG)

    def test_text_enforces_size(self) -> None:
        message = decode(frame("operator_prompt", {"text": "x" * 100}), max_bytes=BIG)

        with pytest.raises(ProtocolError, match="above the"):
            message.text("text", max_bytes=10)

    def test_missing_required_field(self) -> None:
        message = decode(frame("operator_prompt"), max_bytes=BIG)

        with pytest.raises(ProtocolError, match="missing required field"):
            message.text("text", max_bytes=BIG)

    def test_optional_field_defaults_to_empty(self) -> None:
        message = decode(frame("operator_prompt"), max_bytes=BIG)

        assert message.text("text", max_bytes=BIG, required=False) == ""

    def test_boolean_is_not_accepted_as_an_integer(self) -> None:
        message = decode(frame("send_approved", {"turn": True}), max_bytes=BIG)

        with pytest.raises(ProtocolError, match="must be an integer"):
            message.integer("turn")

    def test_integer_rejects_strings(self) -> None:
        message = decode(frame("send_approved", {"turn": "1"}), max_bytes=BIG)

        with pytest.raises(ProtocolError, match="must be an integer"):
            message.integer("turn")

    def test_boolean_rejects_truthy_strings(self) -> None:
        message = decode(frame("send_approved", {"approved": "yes"}), max_bytes=BIG)

        with pytest.raises(ProtocolError, match="must be a boolean"):
            message.boolean("approved")


class TestEncode:
    def test_round_trips_as_json(self) -> None:
        raw = encode(MessageType.STATUS, {"turn": 1})

        parsed = json.loads(raw)
        assert parsed["type"] == "status"
        assert parsed["protocol_version"] == 1
        assert parsed["payload"]["turn"] == 1

    def test_broker_cannot_send_an_inbound_type(self) -> None:
        with pytest.raises(ProtocolError, match="not sent by the broker"):
            encode(MessageType.OPERATOR_PROMPT, {})

    def test_inbound_and_outbound_sets_are_disjoint(self) -> None:
        assert not (INBOUND_TYPES & OUTBOUND_TYPES)


class TestBuildOperation:
    def test_allowed_operation(self) -> None:
        request = build_operation("fill", selector="#msg", value="hello")

        assert request.operation.value == "fill"
        assert request.to_payload()["value"] == "hello"

    @pytest.mark.parametrize("name", ["evaluate", "eval", "goto", "send", "screenshot"])
    def test_operations_off_the_allowlist_refused(self, name: str) -> None:
        with pytest.raises(ValueError, match="is not allowed"):
            build_operation(name, selector="#x")

    def test_press_validates_the_key(self) -> None:
        with pytest.raises(ValueError, match="key .* is not allowed"):
            build_operation("press", selector="#x", key="Control+W")

    def test_press_accepts_an_allowlisted_key(self) -> None:
        assert build_operation("press", selector="#x", key="Enter").key == "Enter"

    def test_element_operations_require_a_selector(self) -> None:
        with pytest.raises(ValueError, match="requires a selector"):
            build_operation("click", selector="  ")
