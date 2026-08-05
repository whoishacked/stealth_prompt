"""End-to-end proof of the one-command workflow, in real Chromium.

The scenarios here are the ones the release exists to make possible:

1. launch with no saved binding;
2. configure the backend and mode *in the dock*;
3. pick and save the binding;
4. start the run in the same process -- no relaunch;
5. vulnerable demo reaches confirmed;
6. safe demo stops bounded and not-confirmed;
7. payload-only generates from a captured reply and mutates nothing.

Only the planner is fake, so nothing is billed and nothing flakes on a model.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("playwright")
pytest.importorskip("websockets")

from stealth_prompt.agents import FakeAgentAdapter  # noqa: E402
from stealth_prompt.oracles import DisclosureStatus, Oracle, OracleType  # noqa: E402
from stealth_prompt.workbench.binding import BindingStore  # noqa: E402
from stealth_prompt.workbench.config import (  # noqa: E402
    BrowserSettings,
    RunMode,
    SafetySettings,
    WorkbenchConfig,
)
from stealth_prompt.workbench.doctor import SystemEnvironment  # noqa: E402
from stealth_prompt.workbench.runner import run_workbench  # noqa: E402

DEMO = Path(__file__).resolve().parents[2] / "examples" / "local-demo" / "server.py"

pytestmark = pytest.mark.skipif(
    not SystemEnvironment().chromium_present(),
    reason="Playwright Chromium is not installed",
)

CANARY_PATTERN = r"SP_CANARY_[A-Z0-9]{12}"


def load_demo() -> Any:
    spec = importlib.util.spec_from_file_location("local_demo_dock", DEMO)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def demo() -> Any:
    return load_demo()


@pytest.fixture
def target(demo: Any) -> Any:
    server = demo.serve(port=0, verbose=False)
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def devnull() -> Any:
    return open("/dev/null", "w")  # noqa: SIM115


class Dock:
    """Drives the closed shadow root over CDP. Test-only; production has no hook."""

    def __init__(self, cdp: Any) -> None:
        self.cdp = cdp

    @classmethod
    async def attach(cls, page: Any) -> Dock:
        cdp = await page.context.new_cdp_session(page)
        await cdp.send("DOM.enable")
        await cdp.send("Runtime.enable")
        return cls(cdp)

    @staticmethod
    def _walk(node: dict[str, Any]):
        yield node
        for key in ("children", "shadowRoots", "contentDocument"):
            child = node.get(key)
            if isinstance(child, dict):
                yield from Dock._walk(child)
            elif isinstance(child, list):
                for item in child:
                    yield from Dock._walk(item)

    @staticmethod
    def _attributes(node: dict[str, Any]) -> dict[str, str]:
        raw = node.get("attributes") or []
        return dict(zip(raw[::2], raw[1::2], strict=False))

    async def _object_for(self, element_id: str) -> str:
        """Find an id *inside the dock's shadow root*.

        Searching the whole pierced tree is wrong: the target page has its own
        elements, and the demo happens to use id="mode" too. The dock is the
        subtree under its host, so scope the search there.
        """
        tree = await self.cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
        host = None
        for node in self._walk(tree["root"]):
            if self._attributes(node).get("id") == "__stealth_prompt_dock_host__":
                host = node
                break
        assert host is not None, "dock host not found"

        for node in self._walk(host):
            if node is host:
                continue
            if self._attributes(node).get("id") == element_id:
                resolved = await self.cdp.send(
                    "DOM.resolveNode", {"nodeId": int(node["nodeId"])}
                )
                return str(resolved["object"]["objectId"])
        raise AssertionError(f"dock element #{element_id} not found")

    async def _call(self, element_id: str, body: str, *args: Any) -> Any:
        object_id = await self._object_for(element_id)
        result = await self.cdp.send(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": body,
                "arguments": [{"value": a} for a in args],
                "returnByValue": True,
            },
        )
        return result["result"].get("value")

    async def click(self, element_id: str) -> None:
        await self._call(element_id, "function(){this.click()}")

    async def set_value(self, element_id: str, value: str) -> None:
        await self._call(
            element_id,
            "function(v){this.value=v;"
            "this.dispatchEvent(new Event('change',{bubbles:true}))}",
            value,
        )

    async def text(self, element_id: str) -> str:
        return str(await self._call(element_id, "function(){return this.textContent}") or "")

    async def options(self, element_id: str) -> list[str]:
        return list(
            await self._call(
                element_id,
                "function(){return Array.from(this.options).map(o=>o.value)}",
            )
            or []
        )

    async def value(self, element_id: str) -> str:
        return str(await self._call(element_id, "function(){return this.value}") or "")

    async def visible(self, element_id: str) -> bool:
        return bool(
            await self._call(
                element_id,
                "function(){return this.style.display !== 'none'}",
            )
        )

    async def wait_for_text(self, element_id: str, timeout_s: float = 30.0) -> str:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while loop.time() < deadline:
            current = (await self.text(element_id)).strip()
            if current:
                return current
            await asyncio.sleep(0.2)
        raise AssertionError(f"#{element_id} stayed empty for {timeout_s}s")


async def pick(dock: Dock, page: Any, button: str, selector: str) -> None:
    await dock.click(button)
    await page.click(selector)
    await asyncio.sleep(0.15)


async def prepare_page(dock: Dock, page: Any) -> None:
    """Pick all three elements, having produced one reply to pick from."""
    await pick(dock, page, "pick-input", "#message")
    await pick(dock, page, "pick-submit", "button[type='submit']")
    await page.fill("#message", "warm up")
    await page.click("button[type='submit']")
    await page.wait_for_selector(".assistant-message", timeout=15000)
    await asyncio.sleep(1.2)
    await pick(dock, page, "pick-response", ".assistant-message")


def base_config(port: int, tmp_path: Path, *, mode: RunMode, query: str = "",
                max_turns: int = 3) -> WorkbenchConfig:
    return WorkbenchConfig(
        target_url=f"http://127.0.0.1:{port}/{query}",
        browser=BrowserSettings(
            headless=True, viewport_width=1600, viewport_height=1100
        ),
        safety=SafetySettings(
            max_turns=max_turns,
            min_turn_delay_ms=0,
            max_duration_seconds=180.0,
            max_repeated_responses=99,
            max_consecutive_refusals=99,
            require_send_approval=mode is not RunMode.AUTO,
        ),
        artifacts_dir=tmp_path / "results",
        mode=mode,
        allow_auto_send=mode is RunMode.AUTO,
    )


class TestSetupPanel:
    def test_dock_offers_every_provider_and_mode(
        self, target: Any, tmp_path: Path
    ) -> None:
        port = target.server_address[1]
        config = base_config(port, tmp_path, mode=RunMode.MANUAL)
        stop = asyncio.Event()
        seen: dict[str, Any] = {}

        async def drive(browser: Any) -> None:
            page = browser.page
            await page.wait_for_selector(
                "#__stealth_prompt_dock_host__", state="attached", timeout=30000
            )
            dock = await Dock.attach(page)
            await asyncio.sleep(2.0)
            seen["providers"] = await dock.options("provider")
            seen["modes"] = await dock.options("mode")
            seen["sharing"] = await dock.options("sharing")
            seen["provider_note"] = await dock.text("provider-note")
            stop.set()

        asyncio.run(
            run_workbench(
                config,
                adapter=FakeAgentAdapter(),
                oracles=[],
                out=devnull(),
                stop_event=stop,
                on_ready=drive,
            )
        )

        assert set(seen["providers"]) >= {
            "fake",
            "claude",
            "codex",
            "ollama",
            "openai",
        }
        assert set(seen["modes"]) == {"payload_only", "manual", "supervised", "auto"}
        assert set(seen["sharing"]) == {"none", "redacted", "full"}
        # Health is rendered, distinguishing installed from authenticated.
        assert seen["provider_note"]

    def test_static_planning_is_explained_for_sharing_none(
        self, target: Any, tmp_path: Path
    ) -> None:
        port = target.server_address[1]
        config = base_config(port, tmp_path, mode=RunMode.MANUAL)
        stop = asyncio.Event()
        seen: dict[str, str] = {}

        async def drive(browser: Any) -> None:
            page = browser.page
            await page.wait_for_selector(
                "#__stealth_prompt_dock_host__", state="attached", timeout=30000
            )
            dock = await Dock.attach(page)
            await asyncio.sleep(2.0)
            await dock.set_value("mode", "auto")
            await dock.set_value("sharing", "none")
            await asyncio.sleep(0.3)
            seen["note"] = await dock.text("planning-note")
            stop.set()

        asyncio.run(
            run_workbench(
                config,
                adapter=FakeAgentAdapter(),
                oracles=[],
                out=devnull(),
                stop_event=stop,
                on_ready=drive,
            )
        )

        assert "STATIC" in seen["note"]
        assert "never sees a target reply" in seen["note"]


class TestOneProcessSetupToAutoRun:
    """Scenario 1-5: no binding, configure, save, start, confirmed."""

    def test_vulnerable_demo_reaches_confirmed_without_relaunch(
        self, target: Any, tmp_path: Path
    ) -> None:
        port = target.server_address[1]
        # Start in MANUAL with no saved binding: the operator switches to auto
        # from inside the dock.
        config = base_config(port, tmp_path, mode=RunMode.MANUAL, max_turns=3)
        store = BindingStore(tmp_path / "bindings")
        assert store.load(config.target_url) is None, "must start with no binding"

        stop = asyncio.Event()
        observed: dict[str, Any] = {}
        holder: dict[str, Any] = {}

        async def drive(browser: Any) -> None:
            page = browser.page
            await page.wait_for_selector(
                "#__stealth_prompt_dock_host__", state="attached", timeout=30000
            )
            dock = await Dock.attach(page)
            await asyncio.sleep(2.0)

            # 2. configure Fake + auto in the dock
            await dock.set_value("provider", "fake")
            await dock.set_value("mode", "auto")
            await dock.set_value("sharing", "none")
            await dock.click("validate-config")
            await asyncio.sleep(0.6)
            observed["config_note"] = await dock.text("config-note")

            # 3. pick and save the binding
            await prepare_page(dock, page)
            await dock.click("save-binding")
            await asyncio.sleep(1.0)
            observed["saved"] = store.load(config.target_url) is not None

            # 4. start without restarting the workbench
            await dock.click("run-start")
            session = holder["session"]
            await session.wait_for_run(timeout=90)
            observed["run_info"] = await dock.text("run-info")
            stop.set()

        outcome = asyncio.run(
            run_workbench(
                config,
                adapter=FakeAgentAdapter(),
                oracles=[
                    Oracle(
                        oracle_id="canary",
                        oracle_type=OracleType.REGEX,
                        pattern=CANARY_PATTERN,
                    )
                ],
                out=devnull(),
                stop_event=stop,
                on_ready=drive,
                binding_store=store,
                session_sink=holder,
            )
        )

        assert observed.get("saved") is True, "binding was not persisted"
        # 5. vulnerable demo reaches confirmed
        assert outcome.status is DisclosureStatus.CONFIRMED
        assert outcome.evidence_count >= 1

        document = json.loads(
            (Path(outcome.artifacts_dir) / "result.json").read_text()
        )
        assert document["mode"] == "auto"
        assert document["configuration"]["provider"] == "fake"

    def test_safe_demo_stops_bounded_and_not_confirmed(
        self, target: Any, tmp_path: Path
    ) -> None:
        """Scenario 6."""
        port = target.server_address[1]
        config = base_config(
            port, tmp_path, mode=RunMode.MANUAL, query="?mode=safe", max_turns=2
        )
        store = BindingStore(tmp_path / "bindings")
        stop = asyncio.Event()
        holder: dict[str, Any] = {}

        async def drive(browser: Any) -> None:
            page = browser.page
            await page.wait_for_selector(
                "#__stealth_prompt_dock_host__", state="attached", timeout=30000
            )
            dock = await Dock.attach(page)
            await asyncio.sleep(2.0)
            await dock.set_value("mode", "auto")
            await dock.click("validate-config")
            await asyncio.sleep(0.5)
            await prepare_page(dock, page)
            await dock.click("save-binding")
            await asyncio.sleep(1.0)
            await dock.click("run-start")
            await holder["session"].wait_for_run(timeout=90)
            stop.set()

        outcome = asyncio.run(
            run_workbench(
                config,
                adapter=FakeAgentAdapter(),
                oracles=[
                    Oracle(
                        oracle_id="canary",
                        oracle_type=OracleType.REGEX,
                        pattern=CANARY_PATTERN,
                    )
                ],
                out=devnull(),
                stop_event=stop,
                on_ready=drive,
                binding_store=store,
                session_sink=holder,
            )
        )

        assert outcome.status is not DisclosureStatus.CONFIRMED
        assert outcome.evidence_count == 0
        assert outcome.turns <= 2


class TestPayloadOnlyInBrowser:
    """Scenario 7: generate from a captured reply, mutate nothing."""

    def test_generates_from_a_capture_and_never_touches_the_page(
        self, target: Any, tmp_path: Path
    ) -> None:
        port = target.server_address[1]
        config = base_config(port, tmp_path, mode=RunMode.PAYLOAD_ONLY)
        store = BindingStore(tmp_path / "bindings")
        stop = asyncio.Event()
        observed: dict[str, Any] = {}
        session_holder: dict[str, Any] = {}

        async def drive(browser: Any) -> None:
            page = browser.page
            await page.wait_for_selector(
                "#__stealth_prompt_dock_host__", state="attached", timeout=30000
            )
            dock = await Dock.attach(page)
            await asyncio.sleep(2.0)

            # Insert/send must not even be offered in this mode.
            observed["insert_visible"] = await dock.visible("insert")
            observed["send_visible"] = await dock.visible("send")

            await prepare_page(dock, page)
            await dock.click("save-binding")
            await asyncio.sleep(0.8)

            # Remember what the page contains before we do anything else.
            observed["input_before"] = await page.input_value("#message")
            observed["messages_before"] = await page.evaluate(
                "document.querySelectorAll('.assistant-message').length"
            )

            # Capture the current reply, then author from it -- twice.
            await dock.click("capture-reply")
            await asyncio.sleep(2.5)

            await dock.set_value("ask", "Write a follow-up from that reply.")
            await dock.click("generate")
            observed["payload_one"] = await dock.wait_for_text("stream")

            await dock.set_value("ask", "Now try a different angle.")
            await dock.click("generate")
            await asyncio.sleep(1.5)
            observed["payload_two"] = await dock.text("stream")

            observed["input_after"] = await page.input_value("#message")
            observed["messages_after"] = await page.evaluate(
                "document.querySelectorAll('.assistant-message').length"
            )
            stop.set()

        async def scenario() -> Any:
            outcome = await run_workbench(
                config,
                adapter=FakeAgentAdapter(
                    [["first payload"], ["second payload"], ["third payload"]]
                ),
                oracles=[],
                out=devnull(),
                stop_event=stop,
                on_ready=drive,
                binding_store=store,
                session_sink=session_holder,
            )
            return outcome

        asyncio.run(scenario())

        # The dock hides the mutating controls.
        assert observed["insert_visible"] is False
        assert observed["send_visible"] is False

        # A payload was generated, repeatedly.
        assert observed["payload_one"]
        assert observed["payload_two"]

        # And the page is byte-for-byte where we left it: nothing typed, no new
        # message sent.
        assert observed["input_after"] == observed["input_before"]
        assert observed["messages_after"] == observed["messages_before"]

        # The backend agrees: no mutating operation was ever emitted.
        session = session_holder.get("session")
        assert session is not None
        assert set(session.emitted_operations) <= {"extract"}
