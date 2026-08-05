"""Ties a workbench session together and guarantees it comes apart cleanly.

Ownership is explicit: this module starts the broker, renders the extension,
and launches the browser, and it is responsible for closing all three plus the
agent process. Shutdown runs in reverse order inside ``finally`` so an
exception, a Ctrl-C, or a closed browser window all converge on the same path
and never orphan a Chromium or an agent child process.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, TextIO

from ..agents.base import AgentAdapter
from ..agents.registry import ProviderSelection, build_adapter, parse_provider
from ..oracles import DisclosureStatus, Oracle
from .artifacts import ArtifactStore, timestamp_slug
from .binding import BindingStore, TargetBinding
from .broker import EXTENSION_ID, EXTENSION_PUBLIC_KEY, Broker
from .browser import BrowserUnavailableError, launch_browser
from .config import RunMode, WorkbenchConfig
from .extension_builder import build_extension
from .redaction import sanitize_for_terminal
from .session import WorkbenchSession


@dataclass
class WorkbenchOutcome:
    """What a finished session produced."""

    status: DisclosureStatus
    turns: int
    evidence_count: int
    artifacts_dir: str
    result: dict[str, Any]


def session_id(prefix: str = "workbench") -> str:
    return f"{prefix}-{timestamp_slug()}"


async def run_workbench(
    config: WorkbenchConfig,
    *,
    adapter: AgentAdapter | None = None,
    oracles: Sequence[Oracle] = (),
    out: TextIO | None = None,
    ready_event: asyncio.Event | None = None,
    stop_event: asyncio.Event | None = None,
    on_ready: Callable[[Any], Awaitable[None]] | None = None,
    binding: TargetBinding | None = None,
    binding_store: BindingStore | None = None,
    session_sink: dict[str, Any] | None = None,
) -> WorkbenchOutcome:
    """Run one interactive workbench session to completion.

    Args:
        adapter: Agent backend. Defaults to the one named in ``config``.
        oracles: Deterministic disclosure rules evaluated on each reply.
        ready_event: Set once the browser is up. Used by tests.
        stop_event: When set, ends the session. Defaults to browser close.
        on_ready: Called with the launched browser once it is up. This is the
            seam the end-to-end test uses to drive the dock; nothing in normal
            operation passes it.
    """
    stream = out if out is not None else sys.stdout

    # Pin the broker to this session's extension origin before anything binds,
    # so the very first handshake is already validated against it.
    config = replace(
        config, broker=config.broker.with_origin(f"chrome-extension://{EXTENSION_ID}")
    )

    store = ArtifactStore(config.artifacts_dir, session_id=session_id())
    store.open()

    # The selected provider *and model* must reach the adapter. Building from
    # the kind alone silently ignored --model and any non-PATH executable.
    if adapter is not None:
        agent = adapter
    else:
        agent = build_adapter(
            ProviderSelection(
                kind=parse_provider(config.agent.provider),
                model=config.agent.model,
                base_url=config.agent.base_url,
            ),
            timeout_ms=config.agent.limits.timeout_ms,
        )
    session = WorkbenchSession(
        config,
        agent,
        oracles=list(oracles),
        store=store,
        binding=binding,
        binding_store=binding_store,
    )
    if session_sink is not None:
        # A seam for tests that need to assert on backend state; nothing in
        # normal operation passes it.
        session_sink["session"] = session
    if binding is not None:
        session.binding_loaded_from = str(
            (binding_store or BindingStore()).path_for(
                config.target_url, config.binding_name
            )
        )

    broker = Broker(config, session)
    extension = None
    browser = None
    finished = stop_event or asyncio.Event()

    def request_stop(*_: object) -> None:
        finished.set()

    loop = asyncio.get_running_loop()
    handlers_installed: list[Any] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, ValueError):
            loop.add_signal_handler(sig, request_stop)
            handlers_installed.append(sig)

    try:
        port = await broker.start()
        extension = build_extension(
            config,
            broker_url=broker.url,
            public_key=EXTENSION_PUBLIC_KEY,
            extension_id=EXTENSION_ID,
        )

        print(f"Broker listening on {config.broker.host}:{port}", file=stream)
        print(f"Artifacts: {store.directory}", file=stream)
        print(f"Opening {config.target_url}", file=stream)

        browser = await launch_browser(config, extension.directory)
        browser.context.on("close", lambda *_: finished.set())

        print(
            "\nThe assistant dock is in the page (bottom right).\n"
            "  1. Pick the input, send button, and reply element.\n"
            "  2. Ask the agent for a payload.\n"
            "  3. Review it, insert it, then approve the send.\n"
            "Close the browser window when you are done.\n",
            file=stream,
        )
        if on_ready is not None:
            await on_ready(browser)
        if ready_event is not None:
            ready_event.set()

        # An automated run ends when its loop ends; a manual one ends when the
        # operator closes the browser. Wait for whichever applies.
        waiters: list[asyncio.Task[Any]] = [asyncio.ensure_future(finished.wait())]
        if config.mode is not RunMode.MANUAL:
            waiters.append(asyncio.ensure_future(_await_engine(session)))
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()

    except BrowserUnavailableError as exc:
        print(f"Cannot launch the browser: {exc}", file=stream)
        raise
    finally:
        for sig in handlers_installed:
            with contextlib.suppress(NotImplementedError, ValueError):
                loop.remove_signal_handler(sig)
        if browser is not None:
            await browser.close()
        await broker.stop()
        if extension is not None:
            extension.cleanup()
        result = await session.finalize()

    print(
        f"\nSession finished: {result['status']} "
        f"({result['turns_completed']} turn(s), {len(result['evidence'])} evidence item(s))",
        file=stream,
    )
    if result.get("stop_reason"):
        print(f"Stop reason: {result['stop_reason']}", file=stream)
    for item in result["evidence"]:
        print(
            f"  - {item['oracle_id']} ({item['oracle_type']}) turn {item['turn']} "
            f"preview={sanitize_for_terminal(item['preview'], limit=40)}",
            file=stream,
        )
    print(f"Result written to {store.directory / 'result.json'}", file=stream)

    return WorkbenchOutcome(
        status=DisclosureStatus(result["status"]),
        turns=result["turns_completed"],
        evidence_count=len(result["evidence"]),
        artifacts_dir=str(store.directory),
        result=result,
    )


async def _await_engine(session: WorkbenchSession) -> None:
    """Resolve once the automated loop has finished.

    Polls rather than awaiting a task directly because the engine task is
    created later, when the extension completes its handshake.
    """
    while True:
        task = session._engine_task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
            return
        await asyncio.sleep(0.2)
