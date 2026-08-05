"""The Core over a real WebSocket.

`test_core.py` drives the handlers directly, which cannot show what the
*transport* accepts. These tests open genuine connections to a listening Core so
the handshake rules — path, Origin, token — and the frame limits are exercised
where they actually run.

Everything is loopback and the Fake provider answers any prompt, so nothing
external is contacted.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest

pytest.importorskip("websockets")

from stealth_prompt.agents import FakeAgentAdapter  # noqa: E402
from stealth_prompt.core.pairing import EXTENSION_ORIGIN_PATTERN  # noqa: E402
from stealth_prompt.core.server import MAX_FRAME_BYTES, WS_PATH, CoreServer  # noqa: E402

# A syntactically valid extension origin: 32 letters in a-p, as Chrome assigns.
EXTENSION_ORIGIN = "chrome-extension://" + ("a" * 32)


def connect(port: int, query: str = "", origin: str = EXTENSION_ORIGIN) -> Any:
    from websockets.asyncio.client import connect as ws_connect

    url = f"ws://127.0.0.1:{port}{WS_PATH}{query}"
    # `origin` is typed as a NewType over str in the websockets stubs.
    return ws_connect(url, origin=cast(Any, origin), max_size=MAX_FRAME_BYTES * 2)


async def frame(socket: Any, kind: str, payload: dict[str, Any] | None = None) -> None:
    await socket.send(
        json.dumps({"protocol_version": 1, "type": kind, "payload": payload or {}})
    )


async def reply(socket: Any, timeout: float = 10.0) -> dict[str, Any]:
    raw = await asyncio.wait_for(socket.recv(), timeout=timeout)
    return dict(json.loads(raw))


async def replies_until(
    socket: Any, expected: set[str], timeout: float = 30.0
) -> list[dict[str, Any]]:
    """Collect frames without losing fast back-to-back Core messages."""
    frames: list[dict[str, Any]] = []
    seen: set[str] = set()
    while not expected.issubset(seen):
        answer = await reply(socket, timeout=timeout)
        frames.append(answer)
        seen.add(str(answer["type"]))
        if answer["type"] == "error":
            raise AssertionError(answer)
    return frames


async def paired_token(server: CoreServer, code: str) -> str:
    """Complete a real pairing handshake and return the issued token."""
    async with connect(server.bound_port, "?pairing=1") as socket:
        await frame(socket, "pair", {"code": code, "origin": EXTENSION_ORIGIN})
        answer = await reply(socket)
    assert answer["type"] == "paired", answer
    return str(answer["payload"]["token"])


def run(scenario: Any) -> Any:
    return asyncio.run(scenario())


class TestHandshake:
    def test_the_extension_origin_pattern_matches_a_real_chrome_id(self) -> None:
        # Guards the fixture above: if the pattern changes, these tests must too.
        assert EXTENSION_ORIGIN_PATTERN.match(EXTENSION_ORIGIN)

    def test_a_wrong_path_is_refused(self, tmp_path: Path) -> None:
        async def scenario() -> str:
            from websockets.asyncio.client import connect as ws_connect
            from websockets.exceptions import InvalidStatus

            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            port = await server.start()
            try:
                async with ws_connect(
                    f"ws://127.0.0.1:{port}/not-the-socket?pairing=1",
                    origin=cast(Any, EXTENSION_ORIGIN),
                ):
                    return "connected"
            except InvalidStatus as exc:
                return f"refused {exc.response.status_code}"
            finally:
                await server.stop()

        assert run(scenario).startswith("refused")

    @pytest.mark.parametrize(
        "origin",
        [
            "http://127.0.0.1:8000",  # the target page
            "https://evil.example",  # an arbitrary site
            "chrome-extension://short",  # malformed extension id
            "null",
        ],
    )
    def test_only_an_extension_origin_may_connect(
        self, tmp_path: Path, origin: str
    ) -> None:
        async def scenario() -> dict[str, Any]:
            from websockets.asyncio.client import connect as ws_connect
            from websockets.exceptions import InvalidStatus

            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            port = await server.start()
            try:
                outcome = "connected"
                try:
                    async with ws_connect(
                        f"ws://127.0.0.1:{port}{WS_PATH}?pairing=1", origin=cast(Any, origin)
                    ):
                        pass
                except InvalidStatus as exc:
                    outcome = f"refused {exc.response.status_code}"
                return {"outcome": outcome, "rejected": list(server.rejected)}
            finally:
                await server.stop()

        seen = run(scenario)

        assert seen["outcome"].startswith("refused"), f"{origin} was allowed in"
        assert any("origin rejected" in reason for reason in seen["rejected"])

    def test_a_connection_without_a_token_or_pairing_flag_is_refused(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> dict[str, Any]:
            from websockets.asyncio.client import connect as ws_connect
            from websockets.exceptions import InvalidStatus

            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            port = await server.start()
            try:
                outcome = "connected"
                try:
                    async with ws_connect(
                        f"ws://127.0.0.1:{port}{WS_PATH}", origin=cast(Any, EXTENSION_ORIGIN)
                    ):
                        pass
                except InvalidStatus as exc:
                    outcome = f"refused {exc.response.status_code}"
                return {"outcome": outcome, "rejected": list(server.rejected)}
            finally:
                await server.stop()

        seen = run(scenario)

        assert seen["outcome"].startswith("refused")

    def test_a_forged_token_is_refused(self, tmp_path: Path) -> None:
        async def scenario() -> dict[str, Any]:
            from websockets.asyncio.client import connect as ws_connect
            from websockets.exceptions import InvalidStatus

            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            port = await server.start()
            server.pairing.start_pairing()
            try:
                outcome = "connected"
                try:
                    async with ws_connect(
                        f"ws://127.0.0.1:{port}{WS_PATH}?token=" + "f" * 64,
                        origin=cast(Any, EXTENSION_ORIGIN),
                    ):
                        pass
                except InvalidStatus as exc:
                    outcome = f"refused {exc.response.status_code}"
                return {"outcome": outcome, "rejected": list(server.rejected)}
            finally:
                await server.stop()

        seen = run(scenario)

        assert seen["outcome"].startswith("refused")
        assert any("token rejected" in reason for reason in seen["rejected"])


class TestPairingOverTheWire:
    def test_a_paired_token_opens_a_session(self, tmp_path: Path) -> None:
        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            await server.start()
            try:
                code = server.pairing.start_pairing()
                token = await paired_token(server, code)
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await frame(socket, "hello")
                    ready = await reply(socket)
                return {"token": token, "ready": ready}
            finally:
                await server.stop()

        seen = run(scenario)

        assert len(seen["token"]) >= 32
        assert seen["ready"]["type"] == "ready"
        assert set(seen["ready"]["payload"]["modes"]) == {
            "payload_only",
            "assist",
            "guided",
            "auto",
        }

    def test_a_code_cannot_be_redeemed_twice(self, tmp_path: Path) -> None:
        """A replayed pairing code must not yield a second token."""

        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            await server.start()
            try:
                code = server.pairing.start_pairing()
                first = await paired_token(server, code)
                async with connect(server.bound_port, "?pairing=1") as socket:
                    await frame(
                        socket, "pair", {"code": code, "origin": EXTENSION_ORIGIN}
                    )
                    second = await reply(socket)
                return {"first": first, "second": second}
            finally:
                await server.stop()

        seen = run(scenario)

        assert len(seen["first"]) >= 32
        assert seen["second"]["type"] == "pair.rejected"
        assert "token" not in json.dumps(seen["second"]["payload"])

    def test_re_pairing_issues_a_different_token(self, tmp_path: Path) -> None:
        """After a restart the operator pairs again and gets fresh credentials."""

        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            await server.start()
            try:
                first = await paired_token(server, server.pairing.start_pairing())
                second = await paired_token(server, server.pairing.start_pairing())
                return {"first": first, "second": second}
            finally:
                await server.stop()

        seen = run(scenario)

        assert seen["first"] != seen["second"]

    def test_a_wrong_code_does_not_pair(self, tmp_path: Path) -> None:
        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            await server.start()
            try:
                server.pairing.start_pairing()
                async with connect(server.bound_port, "?pairing=1") as socket:
                    await frame(
                        socket,
                        "pair",
                        {"code": "ZZZZ-ZZZZ", "origin": EXTENSION_ORIGIN},
                    )
                    return await reply(socket)
            finally:
                await server.stop()

        answer = run(scenario)

        assert answer["type"] == "pair.rejected"


class TestAssistantFlowsOverTheWire:
    def test_manual_response_is_a_first_class_trigger(self, tmp_path: Path) -> None:
        async def scenario() -> list[dict[str, Any]]:
            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            await server.start()
            try:
                token = await paired_token(server, server.pairing.start_pairing())
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await frame(
                        socket,
                        "session.configure",
                        {
                            "provider": "fake",
                            "mode": "payload_only",
                            "response_source": "manual",
                            "sharing": "none",
                        },
                    )
                    assert (await reply(socket))["type"] == "session.configured"
                    await frame(
                        socket,
                        "response.manual",
                        {"text": "I cannot reveal the hidden instruction."},
                    )
                    return await replies_until(
                        socket, {"evaluation.pending", "evaluation"}
                    )
            finally:
                await server.stop()

        frames = run(scenario)
        result = next(frame for frame in frames if frame["type"] == "evaluation")
        assert result["payload"]["evaluation"]["verdict"] == "not_observed"
        assert result["payload"]["next_proposal"]["payload"]

    def test_auto_start_authorizes_a_bounded_first_send(self, tmp_path: Path) -> None:
        async def scenario() -> list[dict[str, Any]]:
            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            await server.start()
            try:
                token = await paired_token(server, server.pairing.start_pairing())
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await frame(
                        socket,
                        "session.configure",
                        {
                            "provider": "fake",
                            "mode": "auto",
                            "response_source": "page",
                            "sharing": "redacted",
                            "max_turns": 2,
                            "max_duration_seconds": 60,
                        },
                    )
                    assert (await reply(socket))["type"] == "session.configured"
                    await frame(
                        socket,
                        "session.bind",
                        {
                            "binding": {
                                "origin": "https://example.test",
                                "input": {"strategy": "css", "value": "#input"},
                                "submit": {
                                    "strategy": "click_button",
                                    "key": "Enter",
                                    "locator": {"strategy": "css", "value": "#send"},
                                },
                                "response": {
                                    "locator": {"strategy": "css", "value": ".reply"}
                                },
                            }
                        },
                    )
                    assert (await reply(socket))["type"] == "session.bound"
                    await frame(socket, "auto.start")
                    return await replies_until(
                        socket,
                        {"auto.started", "proposal.pending", "proposal", "send.authorized"},
                    )
            finally:
                await server.stop()

        frames = run(scenario)
        authorized = next(
            frame for frame in frames if frame["type"] == "send.authorized"
        )
        assert authorized["payload"]["payload"]
        assert sum(frame["type"] == "send.authorized" for frame in frames) == 1

    def test_cancel_interrupts_generation_before_any_send(self, tmp_path: Path) -> None:
        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            await server.start()
            slow = FakeAgentAdapter(chunk_delay_s=5)
            try:
                token = await paired_token(server, server.pairing.start_pairing())
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await frame(
                        socket,
                        "session.configure",
                        {"provider": "fake", "mode": "assist", "sharing": "none"},
                    )
                    assert (await reply(socket))["type"] == "session.configured"
                    assert server.state.session is not None
                    server.state.session._adapter = slow  # noqa: SLF001

                    await frame(socket, "proposal.request")
                    assert (await reply(socket))["type"] == "proposal.pending"
                    await frame(socket, "cancel")
                    cancelled = await reply(socket, timeout=1)
                    try:
                        unexpected = await reply(socket, timeout=0.2)
                    except TimeoutError:
                        unexpected = None
                    return {
                        "cancelled": cancelled,
                        "unexpected": unexpected,
                        "interrupts": slow.interrupt_count,
                    }
            finally:
                await server.stop()

        seen = run(scenario)
        assert seen["cancelled"]["type"] == "cancelled"
        assert seen["unexpected"] is None
        assert seen["interrupts"] == 1


class TestFrameLimits:
    """Malformed traffic must be refused without taking the Core down."""

    @pytest.mark.parametrize(
        "raw",
        [
            "not json at all",
            "[]",
            '{"protocol_version": 99, "type": "hello", "payload": {}}',
            '{"protocol_version": 1, "type": "session.destroy", "payload": {}}',
            '{"protocol_version": 1, "payload": {}}',
        ],
    )
    def test_a_bad_frame_is_refused_and_the_core_survives(
        self, tmp_path: Path, raw: str
    ) -> None:
        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            await server.start()
            try:
                token = await paired_token(server, server.pairing.start_pairing())
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await socket.send(raw)
                    answer = await reply(socket)
                # The Core still serves a following, well-formed connection.
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await frame(socket, "hello")
                    after = await reply(socket)
                return {"answer": answer, "after": after["type"]}
            finally:
                await server.stop()

        seen = run(scenario)

        assert seen["answer"]["type"] == "error"
        assert seen["after"] == "ready", "the Core stopped serving after a bad frame"

    def test_an_oversized_frame_is_refused(self, tmp_path: Path) -> None:
        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            await server.start()
            try:
                token = await paired_token(server, server.pairing.start_pairing())
                huge = json.dumps(
                    {
                        "protocol_version": 1,
                        "type": "response.captured",
                        "payload": {"text": "A" * (MAX_FRAME_BYTES + 1024)},
                    }
                )
                assert len(huge) > MAX_FRAME_BYTES
                outcome = "accepted"
                async with connect(server.bound_port, f"?token={token}") as socket:
                    try:
                        await socket.send(huge)
                        answer = await reply(socket)
                        outcome = str(answer["type"])
                    except Exception as exc:  # connection torn down is also a refusal
                        outcome = f"closed {type(exc).__name__}"
                return {"outcome": outcome}
            finally:
                await server.stop()

        seen = run(scenario)

        assert seen["outcome"] != "accepted"
        assert seen["outcome"].startswith("error") or seen["outcome"].startswith(
            "closed"
        ), seen["outcome"]


class TestScenarioFilesOverTheWire:
    """Export, preview and the boundaries an import must not cross."""

    async def _configured(self, server: CoreServer, socket: Any) -> None:
        await frame(
            socket,
            "session.configure",
            {"provider": "fake", "mode": "assist", "sharing": "none"},
        )
        assert (await reply(socket))["type"] == "session.configured"

    def test_a_scenario_is_exported_separately_from_evidence(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> dict[str, Any]:
            server = CoreServer(
                port=0,
                artifacts_root=tmp_path / "results",
                oracle_patterns=(r"SP_CANARY_[A-Z0-9]{12}",),
            )
            await server.start()
            try:
                token = await paired_token(server, server.pairing.start_pairing())
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await self._configured(server, socket)
                    await frame(
                        socket, "scenario.export", {"name": "Demo canary disclosure"}
                    )
                    return await reply(socket)
            finally:
                await server.stop()

        result = run(scenario)
        assert result["type"] == "scenario.exported"
        document = result["payload"]["document"]
        assert document["kind"] == "stealth_prompt_scenario"
        assert document["name"] == "Demo canary disclosure"
        assert document["provider"] == "fake"
        # The configured deterministic scorer travels; results do not.
        assert document["scorers"][0]["type"] == "regex"
        assert "turns" not in document
        assert "verdict" not in document
        # It is written next to, not inside, the evidence file.
        assert result["payload"]["path"].endswith("scenario.json")

    def test_a_preview_reports_an_origin_mismatch_without_applying_anything(
        self, tmp_path: Path
    ) -> None:
        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            await server.start()
            try:
                token = await paired_token(server, server.pairing.start_pairing())
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await self._configured(server, socket)
                    await frame(socket, "scenario.export", {"name": "Recorded run"})
                    document = (await reply(socket))["payload"]["document"]
                    document["target_origin"] = "https://staging.example"
                    await frame(
                        socket,
                        "scenario.preview",
                        {
                            "document": document,
                            "current_origin": "https://production.example",
                        },
                    )
                    return await reply(socket)
            finally:
                await server.stop()

        result = run(scenario)
        assert result["type"] == "scenario.preview"
        preview = result["payload"]["preview"]
        assert preview["origin_mismatch"] is True
        warning = " ".join(preview["warnings"])
        assert "staging.example" in warning
        assert "production.example" in warning
        assert "in scope" in warning
        # A preview never grants automatic sending.
        assert preview["auto_send_authorized"] is False
        assert preview["requires_revalidation"] is True

    def test_a_version_mismatch_is_a_typed_error(self, tmp_path: Path) -> None:
        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            await server.start()
            try:
                token = await paired_token(server, server.pairing.start_pairing())
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await frame(
                        socket,
                        "scenario.preview",
                        {
                            "document": {
                                "schema_version": 99,
                                "kind": "stealth_prompt_scenario",
                                "name": "from the future",
                            }
                        },
                    )
                    return await reply(socket)
            finally:
                await server.stop()

        result = run(scenario)
        assert result["type"] == "error"
        assert result["payload"]["code"] == "scenario_version"
        assert "version" in result["payload"]["message"]

    def test_a_credential_bearing_scenario_is_refused(self, tmp_path: Path) -> None:
        """An imported file must not be able to smuggle a secret into the Core."""

        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            await server.start()
            try:
                token = await paired_token(server, server.pairing.start_pairing())
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await frame(
                        socket,
                        "scenario.preview",
                        {
                            "document": {
                                "schema_version": 1,
                                "kind": "stealth_prompt_scenario",
                                "name": "sneaky",
                                "objective": "prompt_injection",
                                "provider": "fake",
                                "expected": {"api_key": "sk-live-secret"},
                            }
                        },
                    )
                    return await reply(socket)
            finally:
                await server.stop()

        result = run(scenario)
        assert result["type"] == "error"
        assert result["payload"]["code"] == "scenario_invalid"
        # The rejected value must not be echoed back in the error.
        assert "sk-live-secret" not in json.dumps(result)

    def test_a_malformed_scenario_is_refused(self, tmp_path: Path) -> None:
        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            await server.start()
            try:
                token = await paired_token(server, server.pairing.start_pairing())
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await frame(socket, "scenario.preview", {"document": "{not json"})
                    return await reply(socket)
            finally:
                await server.stop()

        result = run(scenario)
        assert result["type"] == "error"
        assert result["payload"]["code"] == "scenario_invalid"


class TestReportLibraryOverTheWire:
    """Listing and opening stored reports, and what must not cross the socket."""

    def _write_report(self, root: Path, report_id: str, verdict: str = "confirmed") -> None:
        directory = root / report_id
        directory.mkdir(parents=True)
        (directory / "session.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "assistant_session",
                    "session_id": "s1",
                    "exported_at": "2026-08-03T12:00:00+00:00",
                    "verdict": verdict,
                    "configuration": {
                        "origin": "http://127.0.0.1:8765",
                        "objective": "instruction_disclosure",
                        "effective_model": "fake-1",
                        "provider": "fake",
                    },
                    "turns": [{"turn_id": "t1", "response": "SECRET_TARGET_TEXT"}],
                    "timeline": {"events": []},
                }
            )
        )
        (directory / "report.html").write_text("<!doctype html><title>evidence</title>")

    def test_it_lists_stored_reports_with_bounded_metadata(self, tmp_path: Path) -> None:
        root = tmp_path / "results"
        self._write_report(root, "assistant-20260803T120000Z-aaa111")
        self._write_report(root, "assistant-20260803T130000Z-bbb222", verdict="potential")

        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=root)
            await server.start()
            try:
                token = await paired_token(server, server.pairing.start_pairing())
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await frame(socket, "reports.list", {})
                    return await reply(socket)
            finally:
                await server.stop()

        result = run(scenario)
        assert result["type"] == "reports"
        reports = result["payload"]["reports"]
        assert [entry["report_id"] for entry in reports] == [
            "assistant-20260803T130000Z-bbb222",
            "assistant-20260803T120000Z-aaa111",
        ]
        assert reports[0]["verdict"] == "potential"
        assert reports[1]["turns"] == 1
        assert "report.html" in reports[0]["artifacts"]
        # A listing carries metadata, never the captured transcript.
        assert "SECRET_TARGET_TEXT" not in json.dumps(result)

    def test_an_empty_library_is_not_an_error(self, tmp_path: Path) -> None:
        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            await server.start()
            try:
                token = await paired_token(server, server.pairing.start_pairing())
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await frame(socket, "reports.list", {})
                    return await reply(socket)
            finally:
                await server.stop()

        result = run(scenario)
        assert result["type"] == "reports"
        assert result["payload"]["reports"] == []

    def test_it_opens_a_named_report(self, tmp_path: Path) -> None:
        root = tmp_path / "results"
        self._write_report(root, "assistant-20260803T120000Z-aaa111")

        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=root)
            await server.start()
            try:
                token = await paired_token(server, server.pairing.start_pairing())
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await frame(
                        socket,
                        "reports.open",
                        {
                            "report_id": "assistant-20260803T120000Z-aaa111",
                            "artifact": "report.html",
                        },
                    )
                    return await reply(socket)
            finally:
                await server.stop()

        result = run(scenario)
        assert result["type"] == "report"
        assert "<title>evidence</title>" in result["payload"]["content"]
        assert result["payload"]["path"].endswith("report.html")

    @pytest.mark.parametrize(
        "report_id",
        ["../../etc", "assistant-20260803T120000Z-aaa111/../..", "", "results"],
    )
    def test_a_traversal_attempt_is_refused(self, tmp_path: Path, report_id: str) -> None:
        root = tmp_path / "results"
        self._write_report(root, "assistant-20260803T120000Z-aaa111")

        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=root)
            await server.start()
            try:
                token = await paired_token(server, server.pairing.start_pairing())
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await frame(
                        socket,
                        "reports.open",
                        {"report_id": report_id, "artifact": "report.html"},
                    )
                    return await reply(socket)
            finally:
                await server.stop()

        result = run(scenario)
        assert result["type"] == "error"
        assert result["payload"]["code"] == "unknown_report"

    def test_an_unlisted_artifact_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "results"
        self._write_report(root, "assistant-20260803T120000Z-aaa111")

        async def scenario() -> dict[str, Any]:
            server = CoreServer(port=0, artifacts_root=root)
            await server.start()
            try:
                token = await paired_token(server, server.pairing.start_pairing())
                async with connect(server.bound_port, f"?token={token}") as socket:
                    await frame(
                        socket,
                        "reports.open",
                        {
                            "report_id": "assistant-20260803T120000Z-aaa111",
                            "artifact": "../../../etc/passwd",
                        },
                    )
                    return await reply(socket)
            finally:
                await server.stop()

        result = run(scenario)
        assert result["type"] == "error"
        assert result["payload"]["code"] == "unknown_report"

    def test_reports_require_a_paired_token(self, tmp_path: Path) -> None:
        """A page that somehow reached the socket must not read the library."""
        root = tmp_path / "results"
        self._write_report(root, "assistant-20260803T120000Z-aaa111")

        async def scenario() -> str:
            server = CoreServer(port=0, artifacts_root=root)
            await server.start()
            try:
                try:
                    async with connect(server.bound_port, "?token=not-a-real-token"):
                        return "connected"
                except Exception as exc:  # noqa: BLE001 - the class varies by version
                    return type(exc).__name__
            finally:
                await server.stop()

        assert run(scenario) != "connected"
