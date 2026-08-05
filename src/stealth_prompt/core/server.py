"""The local Core: a loopback WebSocket the browser extension talks to.

The Core is authoritative. The extension may *ask* for a provider, a model, a
proposal, or permission to send; the Core decides. Nothing arriving over this
socket can name an executable, a filesystem path, an endpoint, or a shell
command -- those come from the provider registry and the process environment,
and there is no message that carries them.

Connection security, in order of what an attacker must defeat:

1. the listener binds loopback only, so it is not reachable off-machine;
2. ``Origin`` must be the extension's own origin, so a web page cannot connect;
3. a token issued through explicit pairing must be presented;
4. every frame is size-capped, version-checked, and validated before use.

It never launches a browser.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..agents.registry import (
    ProviderError,
    ProviderSelection,
    capability_report,
    discover_models,
    health_report,
    parse_provider,
)
from ..oracles import Oracle, OracleType
from ..workbench.artifacts import ArtifactStore, timestamp_slug
from ..workbench.config import TargetDataSharing
from .assistant import (
    AssistantSession,
    AssistMode,
    InteractionBinding,
    PotentialFindingAction,
    ResponseSource,
    build_session,
)
from .contracts import (
    ContractError,
    Objective,
    ProviderRefused,
    Verdict,
)
from .pairing import EXTENSION_ORIGIN_PATTERN, PairingError, PairingService
from .reports import MAX_REPORTS, ReportError, list_reports, resolve_report
from .scenario_file import (
    MAX_SCENARIO_BYTES,
    ScenarioError,
    ScenarioVersionError,
    parse_scenario,
    scenario_from_session,
)
from .scenarios import objective_catalog
from .timeline import EventKind, EventSource

PROTOCOL_VERSION = 1

#: A fixed default so the extension can find the Core without configuration.
DEFAULT_PORT = 17371
LOOPBACK_HOSTS = ("127.0.0.1", "::1")

MAX_FRAME_BYTES = 1 * 1024 * 1024
WS_PATH = "/ws"

#: Frames the extension may send. Anything else closes nothing but is refused.
INBOUND = frozenset(
    {
        "pair",
        "hello",
        "capabilities.request",
        "providers.health",
        "models.list",
        "session.configure",
        "session.bind",
        "session.conversation",
        "proposal.request",
        "proposal.approve",
        "payload.sent",
        "response.captured",
        "response.manual",
        "auto.start",
        "finding.confirm",
        "session.export",
        "scenario.export",
        "scenario.preview",
        "reports.list",
        "reports.open",
        "session.stop",
        "cancel",
        "ping",
    }
)

LONG_RUNNING = frozenset(
    {"proposal.request", "response.captured", "response.manual", "auto.start"}
)


class CoreError(ValueError):
    """A frame was malformed, oversized, unauthorized, or unknown."""

    def __init__(self, message: str, *, code: str = "bad_request") -> None:
        super().__init__(message)
        self.code = code


def encode(kind: str, payload: dict[str, Any] | None = None, **envelope: Any) -> str:
    return json.dumps(
        {
            "protocol_version": PROTOCOL_VERSION,
            "type": kind,
            "payload": payload or {},
            **envelope,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode(raw: str | bytes) -> tuple[str, dict[str, Any]]:
    """Decode one inbound frame, refusing anything unexpected."""
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(encoded) > MAX_FRAME_BYTES:
        raise CoreError(
            f"frame is {len(encoded)} bytes, above the {MAX_FRAME_BYTES}-byte limit",
            code="too_large",
        )
    try:
        document = json.loads(encoded)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise CoreError("frame is not valid JSON") from None
    if not isinstance(document, dict):
        raise CoreError("frame must be a JSON object")

    version = document.get("protocol_version", PROTOCOL_VERSION)
    if version != PROTOCOL_VERSION:
        raise CoreError(
            f"unsupported protocol version {version!r}; this Core speaks "
            f"version {PROTOCOL_VERSION}",
            code="version_mismatch",
        )

    kind = document.get("type")
    if not isinstance(kind, str) or kind not in INBOUND:
        raise CoreError(f"unknown message type {kind!r}", code="unknown_type")

    payload = document.get("payload", {})
    if not isinstance(payload, dict):
        raise CoreError("'payload' must be an object")
    return kind, payload


def _text(payload: dict[str, Any], key: str, *, limit: int, default: str = "") -> str:
    value = payload.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise CoreError(f"field {key!r} must be a string")
    if len(value.encode("utf-8")) > limit:
        raise CoreError(f"field {key!r} is longer than {limit} bytes")
    return value


def _bounded_int(
    payload: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CoreError(f"field {key!r} must be an integer")
    if not minimum <= value <= maximum:
        raise CoreError(
            f"field {key!r} must be between {minimum} and {maximum}",
            code="invalid_configuration",
        )
    return value


@dataclass
class CoreState:
    """Everything one paired client is working on."""

    session: AssistantSession | None = None
    artifacts_root: Path = field(default_factory=lambda: Path("results"))
    oracle_patterns: tuple[str, ...] = ()

    def build_oracles(self) -> list[Oracle]:
        return [
            Oracle(
                oracle_id=f"regex-{index}",
                oracle_type=OracleType.REGEX,
                pattern=pattern,
            )
            for index, pattern in enumerate(self.oracle_patterns, start=1)
        ]


class CoreServer:
    """Serves the assistant protocol on loopback for a paired extension."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        pairing: PairingService | None = None,
        artifacts_root: Path | None = None,
        oracle_patterns: tuple[str, ...] = (),
        allowed_origin_pattern: Any = EXTENSION_ORIGIN_PATTERN,
    ) -> None:
        if host not in LOOPBACK_HOSTS:
            raise ValueError(
                f"the Core binds loopback only; refusing to bind {host!r}"
            )
        self.host = host
        self.port = port
        self.pairing = pairing or PairingService()
        self.state = CoreState(
            artifacts_root=artifacts_root or Path("results"),
            oracle_patterns=oracle_patterns,
        )
        self._allowed_origin = allowed_origin_pattern
        self._server: Any = None
        self._bound_port: int | None = None
        self.rejected: list[str] = []
        self.accepted = 0

    # ------------------------------------------------------------- transport

    @property
    def bound_port(self) -> int:
        if self._bound_port is None:
            raise RuntimeError("the Core is not started")
        return self._bound_port

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.bound_port}{WS_PATH}"

    def _reject(self, connection: Any, reason: str, status: int = 403) -> Any:
        self.rejected.append(reason)
        return connection.respond(status, "forbidden\n")

    def _process_request(self, connection: Any, request: Any) -> Any:
        """Authorize before the WebSocket handshake completes."""
        parsed = urlparse(request.path)
        if parsed.path != WS_PATH:
            return self._reject(connection, "wrong path", status=404)

        origin = request.headers.get("Origin")
        # A web page must never be able to reach the Core, even on loopback.
        if not origin or not self._allowed_origin.match(origin):
            return self._reject(connection, f"origin rejected: {origin!r}")

        query = parse_qs(parsed.query)
        # Pairing is the one exchange that legitimately has no token yet.
        if query.get("pairing", [""])[0] == "1":
            self.accepted += 1
            return None

        token = query.get("token", [""])[0]
        try:
            self.pairing.verify(token, origin=origin)
        except PairingError as exc:
            return self._reject(connection, f"token rejected: {exc}")

        self.accepted += 1
        return None

    async def start(self) -> int:
        from websockets.asyncio.server import serve

        self._server = await serve(
            self._handle,
            host=self.host,
            port=self.port,
            process_request=self._process_request,
            max_size=MAX_FRAME_BYTES,
            ping_interval=20,
            ping_timeout=20,
        )
        for sock in getattr(self._server, "sockets", None) or []:
            self._bound_port = sock.getsockname()[1]
            break
        if self._bound_port is None:  # pragma: no cover - defensive
            raise RuntimeError("the Core failed to bind a port")
        return self._bound_port

    async def stop(self) -> None:
        if self.state.session is not None:
            await self.state.session.close()
            self.state.session = None
        if self._server is None:
            return
        self._server.close()
        try:
            await asyncio.wait_for(self._server.wait_closed(), timeout=5)
        except (TimeoutError, asyncio.TimeoutError):  # pragma: no cover
            pass
        self._server = None
        self._bound_port = None

    async def __aenter__(self) -> CoreServer:
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()

    async def _handle(self, connection: Any) -> None:
        from websockets.exceptions import ConnectionClosed

        active: asyncio.Task[None] | None = None

        async def safely(kind: str, payload: dict[str, Any]) -> None:
            try:
                await self.dispatch(kind, payload, connection.send)
            except CoreError as exc:
                await connection.send(
                    encode("error", {"code": exc.code, "message": str(exc)})
                )
            except (ContractError, ProviderError, ValueError) as exc:
                await connection.send(
                    encode("error", {"code": "rejected", "message": str(exc)})
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one frame must not kill it
                await connection.send(
                    encode(
                        "error",
                        {"code": "internal", "message": type(exc).__name__},
                    )
                )

        try:
            async for raw in connection:
                try:
                    kind, payload = decode(raw)
                except CoreError as exc:
                    await connection.send(
                        encode("error", {"code": exc.code, "message": str(exc)})
                    )
                    continue

                if active is not None and active.done():
                    active = None
                if active is not None and kind not in {
                    "cancel",
                    "session.stop",
                    "ping",
                }:
                    await connection.send(
                        encode(
                            "error",
                            {
                                "code": "busy",
                                "message": "a provider operation is already in progress",
                            },
                        )
                    )
                    continue
                if kind in LONG_RUNNING:
                    active = asyncio.create_task(safely(kind, payload))
                else:
                    await safely(kind, payload)
                    if (
                        kind in {"cancel", "session.stop"}
                        and active is not None
                        and not active.done()
                    ):
                        active.cancel()
                        await asyncio.gather(active, return_exceptions=True)
                        active = None
        except ConnectionClosed:
            pass
        finally:
            if active is not None and not active.done():
                active.cancel()
                await asyncio.gather(active, return_exceptions=True)

    # ------------------------------------------------------------- dispatch

    async def dispatch(self, kind: str, payload: dict[str, Any], send: Any) -> None:
        """Handle one validated frame. Public so tests need no socket."""
        handler = getattr(self, f"_on_{kind.replace('.', '_')}", None)
        if handler is None:  # pragma: no cover - decode() already refuses these
            raise CoreError(f"no handler for {kind!r}", code="unknown_type")
        await handler(payload, send)

    async def _on_ping(self, payload: dict[str, Any], send: Any) -> None:
        await send(encode("pong"))

    async def _on_pair(self, payload: dict[str, Any], send: Any) -> None:
        """Exchange a pairing code for a scoped token."""
        code = _text(payload, "code", limit=64)
        origin = _text(payload, "origin", limit=200)
        try:
            token = self.pairing.redeem(code, origin=origin)
        except PairingError as exc:
            await send(encode("pair.rejected", {"message": str(exc)}))
            return
        await send(
            encode(
                "paired",
                {
                    "token": token,
                    "protocol_version": PROTOCOL_VERSION,
                    "core_version": _core_version(),
                },
            )
        )

    async def _on_hello(self, payload: dict[str, Any], send: Any) -> None:
        await send(
            encode(
                "ready",
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "core_version": _core_version(),
                    "session": (
                        self.state.session.summary() if self.state.session else None
                    ),
                    "recovery": (
                        self.state.session.recovery() if self.state.session else None
                    ),
                    "modes": [mode.value for mode in AssistMode],
                    "objectives": [objective.value for objective in Objective],
                    "sharing": [policy.value for policy in TargetDataSharing],
                },
            )
        )

    async def _on_capabilities_request(
        self, payload: dict[str, Any], send: Any
    ) -> None:
        await send(
            encode(
                "capabilities",
                {
                    "providers": capability_report(),
                    "objectives": objective_catalog(),
                },
            )
        )

    async def _on_providers_health(self, payload: dict[str, Any], send: Any) -> None:
        await send(encode("providers.health", {"providers": health_report()}))

    async def _on_models_list(self, payload: dict[str, Any], send: Any) -> None:
        """Discover models. A failure is reported, never fatal."""
        raw = _text(payload, "provider", limit=64, default="fake")
        request_id = _text(payload, "request_id", limit=64)
        try:
            kind = parse_provider(raw)
        except ProviderError as exc:
            await send(
                encode(
                    "models",
                    {
                        "provider": raw,
                        "request_id": request_id,
                        "models": [],
                        "error": str(exc),
                    },
                )
            )
            return
        try:
            models = await discover_models(ProviderSelection(kind=kind))
            error = ""
        except Exception as exc:  # noqa: BLE001 - discovery is best effort
            models, error = [], f"model discovery failed ({type(exc).__name__})"
        await send(
            encode(
                "models",
                {
                    "provider": kind.value,
                    "request_id": request_id,
                    "models": models,
                    "error": error,
                },
            )
        )

    async def _on_session_configure(
        self, payload: dict[str, Any], send: Any
    ) -> None:
        """Create or reconfigure the session. Transactional: build then commit."""
        provider = _text(payload, "provider", limit=64, default="fake")
        model = _text(payload, "model", limit=128) or None
        mode_raw = _text(payload, "mode", limit=32, default=AssistMode.ASSIST.value)
        sharing_raw = _text(payload, "sharing", limit=32, default="none")
        response_source_raw = _text(
            payload, "response_source", limit=32, default=ResponseSource.PAGE.value
        )
        potential_action_raw = _text(
            payload,
            "potential_finding_action",
            limit=32,
            default=PotentialFindingAction.REVIEW.value,
        )
        max_turns = _bounded_int(
            payload, "max_turns", default=20, minimum=0, maximum=100
        )
        max_duration_seconds = _bounded_int(
            payload,
            "max_duration_seconds",
            default=0,
            minimum=0,
            maximum=1800,
        )
        objective_raw = _text(
            payload, "objective", limit=64, default=Objective.INSTRUCTION_DISCLOSURE.value
        )
        custom = _text(payload, "custom_objective", limit=1000)

        try:
            mode = AssistMode(mode_raw)
            sharing = TargetDataSharing(sharing_raw)
            response_source = ResponseSource(response_source_raw)
            potential_finding_action = PotentialFindingAction(potential_action_raw)
            objective = Objective(objective_raw)
        except ValueError as exc:
            raise CoreError(str(exc), code="invalid_configuration") from None
        if (
            mode is AssistMode.AUTO
            and max_turns == 0
            and potential_finding_action is PotentialFindingAction.CONTINUE
        ):
            raise CoreError(
                "unlimited turns require pausing or stopping on a potential finding",
                code="unsafe_unbounded_run",
            )

        previous = self.state.session
        store = ArtifactStore(
            self.state.artifacts_root, session_id=f"assistant-{timestamp_slug()}"
        )
        # build_session validates provider/model before anything is replaced.
        session = build_session(
            provider=provider,
            model=model,
            mode=mode,
            response_source=response_source,
            potential_finding_action=potential_finding_action,
            sharing=sharing,
            objective=objective,
            custom_objective=custom,
            oracles=self.state.build_oracles(),
            store=store,
            max_turns=max_turns,
            max_duration_seconds=max_duration_seconds,
        )
        if previous is not None:
            # Carry the reviewed binding across a reconfiguration so the
            # operator does not have to re-pick elements.
            session.binding = previous.binding
            session.origin = previous.origin
            session.conversation = previous.conversation
            await previous.close()

        session.timeline.record(
            EventKind.SESSION_STARTED,
            source=EventSource.OPERATOR,
            provider=provider,
            mode=mode.value,
            response_source=response_source.value,
            sharing=sharing.value,
            objective=objective.value,
        )
        self.state.session = session
        await send(encode("session.configured", {"session": session.summary()}))

    def _require_session(self) -> AssistantSession:
        if self.state.session is None:
            raise CoreError("no session is configured", code="no_session")
        return self.state.session

    async def _on_session_bind(self, payload: dict[str, Any], send: Any) -> None:
        session = self._require_session()
        try:
            binding = InteractionBinding.from_dict(payload.get("binding"))
        except ValueError as exc:
            raise CoreError(str(exc), code="invalid_binding") from None
        binding_ready = (
            binding.complete
            if session.response_source is ResponseSource.PAGE
            else bool(binding.input_locator and binding.submit_locator)
        )
        if not binding_ready:
            raise CoreError(
                (
                    "select the input, send control, and response container"
                    if session.response_source is ResponseSource.PAGE
                    else "select the input and send control"
                ),
                code="incomplete_binding",
            )
        session.bind(binding)
        await send(encode("session.bound", {"session": session.summary()}))

    async def _on_session_conversation(
        self, payload: dict[str, Any], send: Any
    ) -> None:
        session = self._require_session()
        session.record_conversation(_text(payload, "text", limit=MAX_FRAME_BYTES // 2))
        await send(encode("session.status", {"session": session.summary()}))

    async def _on_proposal_request(self, payload: dict[str, Any], send: Any) -> None:
        session = self._require_session()
        instruction = _text(payload, "instruction", limit=4096)
        await send(encode("proposal.pending", {"stage": "generating"}))
        started_at = time.monotonic()
        try:
            proposal = await session.propose(instruction)
        except ProviderRefused as refusal:
            # Never a payload. A separate, actionable outcome.
            await send(
                encode(
                    "proposal.refused",
                    {
                        "provider": session.provider,
                        "excerpt": refusal.excerpt,
                        "message": (
                            "The provider declined to create a proposal. Try a "
                            "different objective, edit a payload by hand, or "
                            "choose another configured provider."
                        ),
                    },
                )
            )
            return
        except ContractError as exc:
            if session.generation_cancelled:
                return
            await send(
                encode("proposal.failed", {"message": str(exc)})
            )
            return
        await send(
            encode(
                "proposal",
                {
                    "proposal": proposal.to_dict(),
                    "session": session.summary(),
                    "elapsed_ms": round((time.monotonic() - started_at) * 1000),
                },
            )
        )
        if session.mode is AssistMode.AUTO and session.auto_authorized:
            await self._authorize_send(
                session, proposal.payload, send, automatic=True
            )

    async def _on_proposal_approve(self, payload: dict[str, Any], send: Any) -> None:
        """Record approval and return the operation the browser may perform."""
        session = self._require_session()
        text = _text(payload, "payload", limit=16 * 1024)
        await self._authorize_send(session, text, send, automatic=False)

    async def _authorize_send(
        self,
        session: AssistantSession,
        text: str,
        send: Any,
        *,
        automatic: bool,
    ) -> None:
        """The only Core path that can authorize a browser mutation."""
        turn = session.approve(text, automatic=automatic)
        binding = session.binding
        if binding is None:  # pragma: no cover - approve() requires a proposal
            raise CoreError("no interaction is bound", code="no_binding")

        await send(
            encode(
                "send.authorized",
                {
                    "turn_id": turn.turn_id,
                    "operation_id": f"op-{secrets.token_hex(6)}",
                    # The browser is told what to type and which bound element
                    # to use. It is never told a selector chosen by a model.
                    "payload": turn.approved_payload,
                    "submit_strategy": binding.submit_strategy,
                    "submit_key": binding.submit_key,
                    "stable_ms": binding.stable_ms,
                    "timeout_ms": binding.timeout_ms,
                },
            )
        )

    async def _on_payload_sent(self, payload: dict[str, Any], send: Any) -> None:
        session = self._require_session()
        session.record_sent()
        await send(encode("session.status", {"session": session.summary()}))

    async def _on_response_captured(self, payload: dict[str, Any], send: Any) -> None:
        session = self._require_session()
        text = _text(payload, "text", limit=MAX_FRAME_BYTES // 2)
        await self._evaluate_and_maybe_propose(
            session, text, send, source=EventSource.BROWSER, force_next=False
        )

    async def _on_response_manual(self, payload: dict[str, Any], send: Any) -> None:
        """Evaluate operator-pasted context and produce the next payload.

        This is a first-class fallback, not a browser-capture impersonation:
        evidence records the operator as the source and no page mutation is
        authorized by this frame.
        """
        session = self._require_session()
        if session.mode is AssistMode.AUTO:
            raise CoreError(
                "manual response trigger is incompatible with auto mode",
                code="invalid_configuration",
            )
        text = _text(payload, "text", limit=MAX_FRAME_BYTES // 2)
        if not text.strip():
            raise CoreError("paste a non-empty bot response", code="empty_response")
        await self._evaluate_and_maybe_propose(
            session, text, send, source=EventSource.OPERATOR, force_next=True
        )

    async def _evaluate_and_maybe_propose(
        self,
        session: AssistantSession,
        text: str,
        send: Any,
        *,
        source: EventSource,
        force_next: bool,
    ) -> None:
        await send(encode("evaluation.pending", {"stage": "evaluating"}))
        started_at = time.monotonic()
        wants_next = force_next or session.mode in {AssistMode.GUIDED, AssistMode.AUTO}
        has_room = session.has_turns_remaining()
        combined = (
            wants_next
            and has_room
            and session.sharing is not TargetDataSharing.NONE
        )
        automatic_proposal = None
        if combined:
            evaluation, automatic_proposal = await session.evaluate_and_propose(
                text, source=source
            )
        else:
            evaluation = await session.evaluate(text, source=source)
        if session.generation_cancelled:
            return
        result: dict[str, Any] = {
            "evaluation": evaluation.to_dict(),
            "session": session.summary(),
            "elapsed_ms": round((time.monotonic() - started_at) * 1000),
            "planning_strategy": "combined" if combined else "sequential",
        }
        if combined:
            if automatic_proposal is not None:
                result["next_proposal"] = automatic_proposal.to_dict()
            elif evaluation.verdict.value != "confirmed":
                result["next_proposal_error"] = (
                    "the provider did not produce a valid combined next proposal"
                )
        auto_stop_reason = ""
        if session.mode is AssistMode.AUTO:
            if evaluation.verdict is Verdict.POTENTIAL:
                if session.potential_finding_action is PotentialFindingAction.REVIEW:
                    auto_stop_reason = "potential_review"
                elif session.potential_finding_action is PotentialFindingAction.STOP:
                    auto_stop_reason = "potential_found"
                else:
                    auto_stop_reason = session.auto_stop_reason()
            else:
                auto_stop_reason = session.auto_stop_reason()
            if not auto_stop_reason and combined and automatic_proposal is None:
                auto_stop_reason = "proposal_failed"
            if auto_stop_reason:
                session.stop_auto()
                result["auto_stopped"] = auto_stop_reason
                if auto_stop_reason in {
                    "potential_found",
                    "confirmed",
                    "max_turns",
                    "max_duration",
                }:
                    session.write_export()
                    result["auto_finished"] = auto_stop_reason
        if (
            wants_next
            and not combined
            and not auto_stop_reason
            and session.has_turns_remaining()
        ):
            try:
                proposal = await session.propose()
                result["next_proposal"] = proposal.to_dict()
                if session.mode is AssistMode.AUTO:
                    automatic_proposal = proposal
            except (ContractError, ProviderRefused) as exc:
                result["next_proposal_error"] = str(exc)
                if session.mode is AssistMode.AUTO:
                    session.stop_auto()
                    result["auto_stopped"] = "proposal_failed"
        await send(encode("evaluation", result))
        if automatic_proposal is not None and session.auto_authorized:
            await self._authorize_send(
                session, automatic_proposal.payload, send, automatic=True
            )

    async def _on_auto_start(self, payload: dict[str, Any], send: Any) -> None:
        session = self._require_session()
        additional_turns = _bounded_int(
            payload, "additional_turns", default=0, minimum=0, maximum=100
        )
        if additional_turns:
            session.extend_auto(additional_turns)
        session.start_auto()
        await send(
            encode(
                "auto.started",
                {
                    "session": session.summary(),
                    "max_turns": session.max_turns,
                    "max_duration_seconds": session.max_duration_seconds,
                },
            )
        )
        pending = session.pending_proposal()
        if pending is not None:
            await self._authorize_send(
                session, pending.payload, send, automatic=True
            )
        else:
            await self._on_proposal_request(
                {"instruction": _text(payload, "instruction", limit=4096)}, send
            )

    async def _on_finding_confirm(self, payload: dict[str, Any], send: Any) -> None:
        session = self._require_session()
        continue_testing = payload.get("continue", False)
        if not isinstance(continue_testing, bool):
            raise CoreError("field 'continue' must be a boolean")
        evaluation = session.confirm_finding(continue_testing=continue_testing)
        await send(
            encode(
                "evaluation",
                {"evaluation": evaluation.to_dict(), "session": session.summary()},
            )
        )
        if continue_testing:
            await self._on_auto_start({}, send)

    async def _on_cancel(self, payload: dict[str, Any], send: Any) -> None:
        session = self.state.session
        if session is not None:
            await session.interrupt()
        await send(encode("cancelled", {}))

    async def _on_session_export(self, payload: dict[str, Any], send: Any) -> None:
        session = self._require_session()
        path = session.write_export()
        await send(
            encode(
                "exported",
                {
                    "path": path,
                    "html_path": session.report_path(),
                    "document": session.export(),
                },
            )
        )

    async def _on_scenario_export(self, payload: dict[str, Any], send: Any) -> None:
        """Emit a replayable scenario, separately from the evidence export."""
        session = self._require_session()
        scenario = scenario_from_session(
            session,
            name=str(payload.get("name", ""))[:120] or f"{session.objective.value} scenario",
            description=str(payload.get("description", ""))[:2000],
        )
        path: str | None = None
        if session.store is not None:
            ref = session.store.write_json("scenario.json", scenario.to_dict())
            path = str(session.store.directory / ref.name)
        await send(
            encode(
                "scenario.exported",
                {"path": path, "document": scenario.to_dict(), "filename": "scenario.json"},
            )
        )

    async def _on_scenario_preview(self, payload: dict[str, Any], send: Any) -> None:
        """Parse an imported scenario and describe it. Nothing is applied here.

        Import is deliberately two steps: the Core validates and summarises, the
        operator reads the summary -- including any origin mismatch -- and only
        then chooses to configure a session from it. A one-step import would let
        a file silently retarget a live assessment.
        """
        document = payload.get("document")
        if isinstance(document, str) and len(document) > MAX_SCENARIO_BYTES:
            raise CoreError("scenario is too large", code="scenario_invalid")
        try:
            scenario = parse_scenario(document if isinstance(document, (str, dict)) else "")
        except ScenarioVersionError as exc:
            raise CoreError(str(exc), code="scenario_version") from None
        except ScenarioError as exc:
            raise CoreError(str(exc), code="scenario_invalid") from None
        current = str(payload.get("current_origin", ""))[:300]
        await send(
            encode(
                "scenario.preview",
                {
                    "preview": scenario.preview(current_origin=current),
                    # The normalized document the panel applies if the operator
                    # accepts. Re-serialized from the parsed form, so nothing
                    # unparsed survives the round trip.
                    "document": scenario.to_dict(),
                },
            )
        )

    async def _on_reports_list(self, payload: dict[str, Any], send: Any) -> None:
        """List previously exported sessions from the artifacts root.

        Derived from the directory listing on every call rather than cached, so
        the panel can never show a report the operator has already deleted from
        disk.
        """
        limit = _bounded_int(payload, "limit", default=50, minimum=1, maximum=MAX_REPORTS)
        summaries = list_reports(self.state.artifacts_root, limit=limit)
        await send(
            encode(
                "reports",
                {
                    "reports": [summary.to_dict() for summary in summaries],
                    "root": str(self.state.artifacts_root),
                    "truncated": len(summaries) >= limit,
                },
            )
        )

    async def _on_reports_open(self, payload: dict[str, Any], send: Any) -> None:
        """Return one report artifact's absolute path and, for HTML, its text.

        The extension cannot open a file:// URL from a Side Panel, so the HTML
        body is returned for the panel to hand to a download. It is sent only on
        explicit request for a named report -- never as part of a listing.
        """
        report_id = _text(payload, "report_id", limit=120)
        artifact = _text(payload, "artifact", limit=40, default="report.html")
        try:
            path = resolve_report(self.state.artifacts_root, report_id, artifact)
        except ReportError as exc:
            raise CoreError(str(exc), code="unknown_report") from None
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise CoreError(f"the report could not be read: {exc}", code="unknown_report") from None
        if len(content.encode("utf-8")) > MAX_FRAME_BYTES // 2:
            raise CoreError("the report is too large to open in the panel", code="too_large")
        await send(
            encode(
                "report",
                {
                    "report_id": report_id,
                    "artifact": artifact,
                    "path": str(path),
                    "content": content,
                },
            )
        )

    async def _on_session_stop(self, payload: dict[str, Any], send: Any) -> None:
        session = self.state.session
        if session is not None:
            session.stop_auto()
            await session.interrupt()
            session.timeline.record(
                EventKind.SESSION_STOPPED, source=EventSource.OPERATOR
            )
            session.write_export()
            await session.close()
            self.state.session = None
        await send(encode("session.stopped", {}))


def _core_version() -> str:
    from .. import __version__

    return __version__
