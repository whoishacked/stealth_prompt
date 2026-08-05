"""OpenAI-compatible HTTP backend for the workbench.

The API key lives in this process and nowhere else. It is read from the
environment by the registry, held in memory here, and sent only in the
``Authorization`` header of a request to the configured endpoint. It is never
written to a binding, an artifact, a log line, a status message, a model list,
or any frame the extension can see -- the adapter has no method that returns it
and ``__repr__`` omits it.

Streaming uses the documented ``chat/completions`` SSE format, which every
OpenAI-compatible server implements. ``stream_options.include_usage`` asks for a
usage block; when the server does not send one, usage is reported as unavailable
rather than estimated.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, ClassVar

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

DEFAULT_MODEL = "gpt-4o-mini"
CHAT_PATH = "/chat/completions"
MODELS_PATH = "/models"

MAX_LINE_BYTES = 1 * 1024 * 1024


@dataclass
class _Credential:
    """Holds the key and keeps it out of reprs and tracebacks."""

    value: str = field(repr=False)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "<api key redacted>"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return "<api key redacted>"


def _request(
    url: str, key: str, document: dict[str, Any] | None, *, method: str
) -> Any:
    data = json.dumps(document).encode("utf-8") if document is not None else None
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if document else "application/json",
    }
    request = urllib.request.Request(  # noqa: S310 - scheme validated by registry
        url, data=data, headers=headers, method=method
    )
    return request


async def list_openai_models(
    base_url: str, api_key: str, *, timeout_s: float = 10.0
) -> list[dict[str, Any]]:
    """List models the endpoint offers. Returns ``[]`` on any failure."""

    def _fetch() -> list[dict[str, Any]]:
        try:
            request = _request(f"{base_url}{MODELS_PATH}", api_key, None, method="GET")
            with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
                document = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            return []
        data = document.get("data") if isinstance(document, dict) else None
        if not isinstance(data, list):
            return []
        models: list[dict[str, Any]] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            identifier = entry.get("id")
            if isinstance(identifier, str) and identifier:
                models.append(
                    {"id": identifier, "label": identifier, "default": False}
                )
        models.sort(key=lambda item: item["id"])
        return models

    return await asyncio.to_thread(_fetch)


class OpenAIAdapter:
    """Streams a bounded chat completion from an OpenAI-compatible endpoint."""

    adapter_name: ClassVar[str] = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str | None = None,
        timeout_ms: int = 120_000,
    ) -> None:
        self._credential = _Credential(api_key)
        self._base_url = base_url.rstrip("/")
        self._model = model or DEFAULT_MODEL
        self._timeout_ms = timeout_ms
        self._started = False
        self._closed = False
        self._interrupt = asyncio.Event()
        self.session_id: str | None = None
        self.last_usage: AgentUsage | None = None
        self.effective_model: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"OpenAIAdapter(base_url={self._base_url!r}, model={self._model!r})"

    async def preflight(self) -> AgentPreflight:
        return AgentPreflight(
            adapter_name=self.adapter_name,
            available=True,
            detail=f"configured for {self._base_url}",
        )

    async def start(self) -> None:
        if self._closed:
            raise AgentStateError("cannot start a closed agent session")
        self._started = True
        self._interrupt.clear()
        self.session_id = f"openai-{self._model}"

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
            payload = {
                "model": self._model,
                "messages": [{"role": "user", "content": request.prompt}],
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            try:
                http = _request(
                    f"{self._base_url}{CHAT_PATH}",
                    self._credential.value,
                    payload,
                    method="POST",
                )
                with urllib.request.urlopen(  # noqa: S310
                    http, timeout=self._timeout_ms / 1000
                ) as response:
                    for raw in response:
                        if len(raw) > MAX_LINE_BYTES:
                            continue
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data:"):
                            continue
                        body = line[len("data:") :].strip()
                        if body == "[DONE]":
                            break
                        try:
                            document = json.loads(body)
                        except json.JSONDecodeError:
                            continue
                        loop.call_soon_threadsafe(queue.put_nowait, ("chunk", document))
            except urllib.error.HTTPError as exc:
                # The status is safe to surface; the body may echo the request.
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
                            f"openai request failed: {value}"
                        ).as_info(retryable=True),
                        sequence=sequence,
                    )
                    return

                if kind == "done":
                    break

                model_name = value.get("model")
                if isinstance(model_name, str) and model_name:
                    self.effective_model = model_name

                reported = value.get("usage")
                if isinstance(reported, dict):
                    usage = AgentUsage(
                        input_tokens=int(reported.get("prompt_tokens") or 0),
                        output_tokens=int(reported.get("completion_tokens") or 0),
                        # The API reports tokens, not money. Deriving a price
                        # from a hard-coded table would be a guess, and the
                        # cost ceiling must not be enforced against a guess.
                        cost_usd=None,
                    )

                choices = value.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
                if not isinstance(delta, dict):
                    continue
                content = delta.get("content")
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
        finally:
            worker.cancel()

        if not accumulator.text:
            yield AgentEvent(
                kind=AgentEventKind.ERROR,
                session_id=self.session_id,
                error=AgentProtocolError("the endpoint produced no message").as_info(),
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
