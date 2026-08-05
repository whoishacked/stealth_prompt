"""Shared child-process plumbing for CLI-backed agents.

Both the Claude and Codex adapters drive a local CLI over stdio with
line-delimited JSON. The rules they share live here:

* processes are spawned with an argv list and ``shell=False`` -- no string is
  ever handed to a shell, so no prompt text can become a command;
* stdout is read line by line and parsed as JSON; anything that is not a
  documented structured event is skipped, never pattern-matched out of prose;
* stderr is drained into a bounded ring buffer for diagnostics, so a chatty
  child cannot exhaust memory and a crash still has context;
* shutdown terminates and then kills, and always reaps.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

#: Longest single JSON line accepted from a child. Chat responses are far
#: smaller; anything larger is a runaway or a framing bug.
MAX_LINE_BYTES = 4 * 1024 * 1024
STDERR_RING_LINES = 50
TERMINATE_GRACE_S = 5.0


class ProcessAgent:
    """A child CLI process speaking line-delimited JSON on stdio."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if not argv:
            raise ValueError("argv cannot be empty")
        self._argv = list(argv)
        self._cwd = cwd
        self._env = dict(env) if env is not None else None
        self._process: asyncio.subprocess.Process | None = None
        self._stderr: deque[str] = deque(maxlen=STDERR_RING_LINES)
        self._stderr_task: asyncio.Task[None] | None = None

    @property
    def argv(self) -> list[str]:
        return list(self._argv)

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr)

    async def start(self) -> None:
        """Spawn the child. No shell is involved."""
        if self._process is not None:
            return
        environment = dict(os.environ) if self._env is None else self._env
        self._process = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=environment,
            limit=MAX_LINE_BYTES,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self._process is not None
        stream = self._process.stderr
        if stream is None:  # pragma: no cover - defensive
            return
        try:
            while True:
                line = await stream.readline()
                if not line:
                    return
                self._stderr.append(line.decode("utf-8", errors="replace").rstrip())
        except (asyncio.CancelledError, ValueError):
            return

    async def write_json(self, document: Any) -> None:
        """Send one JSON line to the child's stdin."""
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("process is not started")
        payload = json.dumps(document, ensure_ascii=False) + "\n"
        self._process.stdin.write(payload.encode("utf-8"))
        await self._process.stdin.drain()

    async def close_stdin(self) -> None:
        if self._process is not None and self._process.stdin is not None:
            with_suppress = self._process.stdin
            try:
                with_suppress.close()
            except (BrokenPipeError, RuntimeError):
                pass

    async def read_json_lines(self) -> AsyncIterator[dict[str, Any]]:
        """Yield each documented JSON object the child writes to stdout.

        Non-JSON lines are skipped rather than interpreted: a banner, a spinner,
        or a colour code is not a protocol event.
        """
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("process is not started")
        stream = self._process.stdout
        while True:
            try:
                line = await stream.readline()
            except ValueError:
                # Line exceeded the buffer limit; skip it rather than die.
                continue
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if not text or not text.startswith("{"):
                continue
            try:
                document = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(document, dict):
                yield document

    async def terminate(self) -> None:
        """Stop the child, escalating to kill, and always reap it."""
        process = self._process
        self._process = None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with_task = self._stderr_task
            self._stderr_task = None
            try:
                await with_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if process is None or process.returncode is not None:
            return

        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=TERMINATE_GRACE_S)
            return
        except (TimeoutError, asyncio.TimeoutError):
            pass
        try:
            process.kill()
        except ProcessLookupError:
            return
        with_wait = process.wait()
        try:
            await asyncio.wait_for(with_wait, timeout=TERMINATE_GRACE_S)
        except (TimeoutError, asyncio.TimeoutError):  # pragma: no cover - defensive
            pass
