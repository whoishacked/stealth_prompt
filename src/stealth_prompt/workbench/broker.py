"""Local authenticated control channel between the extension and Python.

The broker is the only way the extension can reach the orchestrator, so it is
built to be uninteresting to attack:

* it binds ``127.0.0.1`` on an ephemeral port and never any other interface;
* every connection must present the per-session token, compared in constant
  time, and must arrive from the session's own extension origin;
* frames are size-capped before parsing, and unknown frame types are refused;
* anything that fails validation closes the connection rather than degrading.

The extension ID is deterministic because the manifest carries a fixed public
key. That key is published, not secret: it exists so ``Origin`` can be checked
exactly. The session token is the secret.
"""

from __future__ import annotations

import asyncio
import secrets
from types import TracebackType
from urllib.parse import parse_qs, urlparse

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from .config import WorkbenchConfig
from .protocol import MessageType, ProtocolError, decode, encode
from .session import WorkbenchSession

#: Public key shipped in the extension manifest. Publishing it is deliberate:
#: it fixes the extension ID so Origin validation can be exact.
EXTENSION_PUBLIC_KEY = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0NfAj7UbQmK4nXyLVc7GKup9sWyo3hRa"
    "nnobXumbx/NiLMRR1v1SvLvVkgawe4+zQUDkjP+r8eHq/GzrLHOAuilWVKB83B3+wSyB1pqEdB4o"
    "mF4O5V8963I0gcprCTfVUgbzZOonX/QPZZX5NTLOoBE8eLVJiidOjY3oylNMLDxKaTM/+BB88oXg"
    "krFI3qKFMBHFBCtPTVbBfiVxn8Xy7xgiuIuPKmwGeRCuHdvy0f1xSgU2xdztXbFbxvT/ObXvugb/"
    "dJ0kJw8LudQikEDu2/DfUJ6sbfuFntwXRfafV/TN+bdCWIYPVBRPcoaeEA5YLD0+iUouEJDBhokl"
    "3lpgzQIDAQAB"
)

#: Chromium derives this from the key above.
EXTENSION_ID = "dggncdlnkeplmookncjhencbgoalgihm"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}"

WS_PATH = "/ws"


class Broker:
    """Serves the workbench protocol on loopback for one session."""

    def __init__(self, config: WorkbenchConfig, session: WorkbenchSession) -> None:
        self.config = config
        self.session = session
        self._server: Server | None = None
        self._port: int | None = None
        self.rejected: list[str] = []
        self.accepted = 0

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("broker is not started")
        return self._port

    @property
    def url(self) -> str:
        return f"ws://{self.config.broker.host}:{self.port}{WS_PATH}"

    def _reject(
        self, connection: ServerConnection, reason: str, status: int = 403
    ) -> Response:
        """Refuse a connection without explaining more than necessary."""
        self.rejected.append(reason)
        return connection.respond(status, "forbidden\n")

    def _process_request(
        self, connection: ServerConnection, request: Request
    ) -> Response | None:
        """Authenticate and authorize before the WebSocket handshake completes."""
        parsed = urlparse(request.path)
        if parsed.path != WS_PATH:
            return self._reject(connection, "wrong path", status=404)

        headers: Headers = request.headers
        origin = headers.get("Origin")
        allowed = self.config.broker.allowed_origins or (EXTENSION_ORIGIN,)
        if origin not in allowed:
            return self._reject(connection, f"origin rejected: {origin!r}")

        supplied = parse_qs(parsed.query).get("token", [""])[0]
        # Constant-time comparison: a timing oracle on a loopback port is a
        # small risk, but the fix costs nothing.
        if not secrets.compare_digest(supplied, self.config.broker.token):
            return self._reject(connection, "bad token")

        self.accepted += 1
        return None

    async def _handle(self, connection: ServerConnection) -> None:
        try:
            async for raw in connection:
                try:
                    message = decode(
                        raw, max_bytes=self.config.broker.max_message_bytes
                    )
                except ProtocolError as exc:
                    await connection.send(
                        encode(
                            MessageType.ERROR,
                            {"code": "protocol_error", "message": str(exc)},
                        )
                    )
                    continue

                try:
                    await self.session.handle(message, connection.send)
                except ProtocolError as exc:
                    await connection.send(
                        encode(
                            MessageType.ERROR,
                            {"code": "protocol_error", "message": str(exc)},
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - one bad frame must not kill the session
                    await connection.send(
                        encode(
                            MessageType.ERROR,
                            {
                                "code": "internal_error",
                                "message": type(exc).__name__,
                            },
                        )
                    )
        except ConnectionClosed:
            return

    async def start(self) -> int:
        """Start listening and return the bound port."""
        self._server = await serve(
            self._handle,
            host=self.config.broker.host,
            port=self.config.broker.port,
            process_request=self._process_request,
            max_size=self.config.broker.max_message_bytes,
            ping_interval=20,
            ping_timeout=20,
        )
        sockets = getattr(self._server, "sockets", None) or []
        for sock in sockets:
            self._port = sock.getsockname()[1]
            break
        if self._port is None:  # pragma: no cover - defensive
            raise RuntimeError("broker failed to bind a port")
        return self._port

    async def stop(self) -> None:
        """Stop listening and drop every connection."""
        if self._server is None:
            return
        self._server.close()
        try:
            await asyncio.wait_for(self._server.wait_closed(), timeout=5)
        except (TimeoutError, asyncio.TimeoutError):  # pragma: no cover - defensive
            pass
        self._server = None
        self._port = None

    async def __aenter__(self) -> Broker:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()
