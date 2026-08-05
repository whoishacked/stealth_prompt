"""Ollama backend for the workbench.

A first-class :class:`~stealth_prompt.agents.base.AgentAdapter`, not a call into
the legacy ``LLMClient``: the workbench needs streaming, an absolute per-turn
deadline, interruption, and typed errors, and the legacy client offers none of
those.

Ollama is the one external-looking provider that is not an external disclosure,
because it runs on this machine. That is only true while the endpoint is
loopback, so the URL is validated by the registry before an adapter is built
and re-checked here.

Uses ``urllib`` in a worker thread rather than adding an async HTTP dependency
for one backend; the surface used is a single streaming POST.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from typing import Any, ClassVar
from urllib.parse import urlparse

from .base import (
    AgentEvent,
    AgentEventKind,
    AgentPreflight,
    AgentProtocolError,
    AgentRequest,
    AgentStateError,
    AgentTimeoutError,
    AgentUnavailableError,
    AgentUsage,
    TurnAccumulator,
)

DEFAULT_MODEL = "llama3"
CHAT_PATH = "/api/chat"
TAGS_PATH = "/api/tags"

#: Ceiling on a single streamed line, so a malformed server cannot exhaust
#: memory one "chunk" at a time.
MAX_LINE_BYTES = 1 * 1024 * 1024


def _require_loopback(base_url: str) -> None:
    host = urlparse(base_url).hostname or ""
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise AgentUnavailableError(
            f"refusing to use a non-loopback Ollama endpoint: {base_url!r}"
        )


def _post_json(url: str, document: dict[str, Any], timeout_s: float) -> Any:
    request = urllib.request.Request(  # noqa: S310 - scheme validated by caller
        url,
        data=json.dumps(document).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # The streaming reader owns this response and closes it after consuming the
    # NDJSON body. Returning it from a `with` block closes it before iteration.
    return urllib.request.urlopen(request, timeout=timeout_s)  # noqa: S310


def _get_json(url: str, timeout_s: float) -> Any:
    request = urllib.request.Request(url, method="GET")  # noqa: S310
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


async def list_ollama_models(
    base_url: str, *, timeout_s: float = 5.0
) -> list[dict[str, Any]]:
    """List locally pulled models. Returns ``[]` when the server is unreachable."""
    _require_loopback(base_url)

    def _fetch() -> list[dict[str, Any]]:
        try:
            document = _get_json(f"{base_url}{TAGS_PATH}", timeout_s)
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            return []
        models = document.get("models") if isinstance(document, dict) else None
        if not isinstance(models, list):
            return []
        found: list[dict[str, Any]] = []
        for entry in models:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or entry.get("model")
            if isinstance(name, str) and name:
                found.append({"id": name, "label": name, "default": False})
        return found

    return await asyncio.to_thread(_fetch)


class OllamaAdapter:
    """Streams a bounded chat completion from a local Ollama server."""

    adapter_name: ClassVar[str] = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str | None = None,
        timeout_ms: int = 120_000,
    ) -> None:
        _require_loopback(base_url)
        self._base_url = base_url.rstrip("/")
        self._model = model or DEFAULT_MODEL
        self._timeout_ms = timeout_ms
        self._started = False
        self._closed = False
        self._interrupt = asyncio.Event()
        self.session_id: str | None = None
        self.last_usage: AgentUsage | None = None
        self.effective_model: str | None = None

    async def preflight(self) -> AgentPreflight:
        models = await list_ollama_models(self._base_url)
        if not models:
            return AgentPreflight(
                adapter_name=self.adapter_name,
                available=False,
                detail=f"no models reachable at {self._base_url}",
                remedy="start Ollama (`ollama serve`) and pull a model",
            )
        return AgentPreflight(
            adapter_name=self.adapter_name,
            available=True,
            detail=f"{len(models)} model(s) at {self._base_url}",
        )

    async def start(self) -> None:
        if self._closed:
            raise AgentStateError("cannot start a closed agent session")
        self._started = True
        self._interrupt.clear()
        self.session_id = f"ollama-{self._model}"

    async def send(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        if self._closed:
            raise AgentStateError("cannot send to a closed agent session")
        if not self._started:
            raise AgentStateError("call start() before send()")

        self._interrupt.clear()
        accumulator = TurnAccumulator(max_output_bytes=request.max_output_bytes)
        sequence = 0

        yield AgentEvent(
            kind=AgentEventKind.SESSION_STARTED,
            session_id=self.session_id,
            sequence=sequence,
        )
        sequence += 1

        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + request.timeout_ms / 1000

        def _stream() -> None:
            """Read the newline-delimited stream on a worker thread."""
            payload = {
                "model": self._model,
                "messages": [{"role": "user", "content": request.prompt}],
                "stream": True,
            }
            try:
                response = _post_json(
                    f"{self._base_url}{CHAT_PATH}", payload, self._timeout_ms / 1000
                )
                with response:
                    for raw in response:
                        if len(raw) > MAX_LINE_BYTES:
                            continue
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line or not line.startswith("{"):
                            continue
                        try:
                            document = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        loop.call_soon_threadsafe(queue.put_nowait, ("chunk", document))
                        if document.get("done"):
                            break
            except urllib.error.HTTPError as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait, ("error", f"HTTP {exc.code}")
                )
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait, ("error", type(exc).__name__)
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

        worker = asyncio.create_task(asyncio.to_thread(_stream))
        usage: AgentUsage | None = None

        try:
            while True:
                if self._interrupt.is_set():
                    yield AgentEvent(
                        kind=AgentEventKind.INTERRUPTED,
                        text=accumulator.text,
                        session_id=self.session_id,
                        sequence=sequence,
                    )
                    return

                remaining = deadline - loop.time()
                if remaining <= 0:
                    yield AgentEvent(
                        kind=AgentEventKind.ERROR,
                        session_id=self.session_id,
                        error=AgentTimeoutError(
                            f"agent did not finish within {request.timeout_ms} ms"
                        ).as_info(retryable=True),
                        sequence=sequence,
                    )
                    return

                try:
                    kind, value = await asyncio.wait_for(
                        queue.get(), timeout=min(remaining, 1.0)
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    continue

                if kind == "error":
                    yield AgentEvent(
                        kind=AgentEventKind.ERROR,
                        session_id=self.session_id,
                        error=AgentUnavailableError(
                            f"ollama request failed: {value}"
                        ).as_info(retryable=True),
                        sequence=sequence,
                    )
                    return

                if kind == "done":
                    break

                message = value.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content:
                        accepted = accumulator.add(content)
                        if accepted:
                            yield AgentEvent(
                                kind=AgentEventKind.TEXT_DELTA,
                                text=accepted,
                                session_id=self.session_id,
                                sequence=sequence,
                            )
                            sequence += 1
                model_name = value.get("model")
                if isinstance(model_name, str) and model_name:
                    self.effective_model = model_name
                if value.get("done"):
                    prompt_tokens = value.get("prompt_eval_count")
                    output_tokens = value.get("eval_count")
                    if isinstance(prompt_tokens, int) or isinstance(output_tokens, int):
                        usage = AgentUsage(
                            input_tokens=int(prompt_tokens or 0),
                            output_tokens=int(output_tokens or 0),
                            # A local model has no billable cost to report, and
                            # inventing one would be worse than reporting none.
                            cost_usd=None,
                        )
                    break
        finally:
            worker.cancel()

        if not accumulator.text:
            yield AgentEvent(
                kind=AgentEventKind.ERROR,
                session_id=self.session_id,
                error=AgentProtocolError("ollama produced no message").as_info(),
                sequence=sequence,
            )
            return

        self.last_usage = usage
        yield AgentEvent(
            kind=AgentEventKind.MESSAGE_COMPLETED,
            text=accumulator.text,
            session_id=self.session_id,
            truncated=accumulator.truncated,
            sequence=sequence,
        )
        sequence += 1

        if usage is not None:
            yield AgentEvent(
                kind=AgentEventKind.USAGE,
                session_id=self.session_id,
                usage=usage,
                sequence=sequence,
            )

    async def interrupt(self) -> None:
        self._interrupt.set()

    async def close(self) -> None:
        self._closed = True
        self._started = False
        self._interrupt.set()
