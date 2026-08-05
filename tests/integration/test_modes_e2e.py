"""End-to-end tests for bindings and the automated modes.

Real Chromium, the real extension, the real broker, the real demo target. Only
the planner is deterministic, so these cost nothing and cannot flake on a model.
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
from stealth_prompt.workbench.binding import (  # noqa: E402
    BindingStore,
    BoundLocator,
    CaptureSettings,
    TargetBinding,
)
from stealth_prompt.workbench.config import (  # noqa: E402
    BrowserSettings,
    RunMode,
    SafetySettings,
    WorkbenchConfig,
)
from stealth_prompt.workbench.doctor import SystemEnvironment  # noqa: E402
from stealth_prompt.workbench.operations import (  # noqa: E402
    LocatorStrategy,
    SubmitAction,
    SubmitStrategy,
)
from stealth_prompt.workbench.runner import run_workbench  # noqa: E402
from stealth_prompt.workbench.state import StopReason  # noqa: E402

DEMO = Path(__file__).resolve().parents[2] / "examples" / "local-demo" / "server.py"

pytestmark = pytest.mark.skipif(
    not SystemEnvironment().chromium_present(),
    reason="Playwright Chromium is not installed",
)

CANARY_PATTERN = r"SP_CANARY_[A-Z0-9]{12}"


def load_demo() -> Any:
    spec = importlib.util.spec_from_file_location("local_demo_modes", DEMO)
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


def demo_binding(origin: str) -> TargetBinding:
    """The binding an operator would have saved for the demo page."""
    return TargetBinding(
        target_origin=origin,
        input=BoundLocator(
            strategy=LocatorStrategy.ROLE,
            value="textbox",
            name="Message",
            css_fallback="#message",
        ),
        submit_locator=BoundLocator(
            strategy=LocatorStrategy.ROLE,
            value="button",
            name="Send",
            css_fallback="button[type='submit']",
        ),
        # The demo's Send button is a real form submit, but clicking is the
        # strategy that also works on non-form React buttons.
        submit_action=SubmitAction(strategy=SubmitStrategy.CLICK_BUTTON),
        response_locator=BoundLocator(
            strategy=LocatorStrategy.CSS, value=".assistant-message", pick="last"
        ),
        capture=CaptureSettings(stable_ms=1500, timeout_ms=30000),
    )


def make_config(
    port: int,
    tmp_path: Path,
    *,
    mode: RunMode,
    max_turns: int = 3,
    mode_query: str = "",
    max_duration_seconds: float | None = 300.0,
) -> WorkbenchConfig:
    url = f"http://127.0.0.1:{port}/{mode_query}"
    return WorkbenchConfig(
        target_url=url,
        browser=BrowserSettings(
            headless=True, viewport_width=1600, viewport_height=1000
        ),
        safety=SafetySettings(
            max_turns=max_turns,
            min_turn_delay_ms=0,
            max_duration_seconds=max_duration_seconds,
            max_repeated_responses=99,
            max_consecutive_refusals=99,
            require_send_approval=mode is not RunMode.AUTO,
        ),
        artifacts_dir=tmp_path / "results",
        mode=mode,
        allow_auto_send=mode is RunMode.AUTO,
    )


async def wait_for_dock(browser: Any) -> None:
    await browser.page.wait_for_selector(
        "#__stealth_prompt_dock_host__", state="attached", timeout=30000
    )


def devnull() -> Any:
    return open("/dev/null", "w")  # noqa: SIM115


class TestBindingPersistence:
    def test_binding_saved_then_loaded_on_a_second_run(
        self, target: Any, tmp_path: Path
    ) -> None:
        """A reviewed binding survives into a fresh session and browser profile."""
        port = target.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        store = BindingStore(tmp_path / "bindings")

        # First run: the operator saves the setup.
        store.save(demo_binding(origin))

        # Second run: a brand-new store instance and a fresh browser profile
        # still find it.
        reloaded = BindingStore(tmp_path / "bindings").load(origin)
        assert reloaded is not None

        config = make_config(port, tmp_path, mode=RunMode.MANUAL)
        stop = asyncio.Event()
        seen: dict[str, Any] = {}

        async def drive(browser: Any) -> None:
            await wait_for_dock(browser)
            seen["ok"] = True
            stop.set()

        outcome = asyncio.run(
            run_workbench(
                config,
                adapter=FakeAgentAdapter(),
                oracles=[],
                out=devnull(),
                stop_event=stop,
                on_ready=drive,
                binding=reloaded,
                binding_store=store,
            )
        )

        assert seen.get("ok") is True
        document = json.loads(
            (Path(outcome.artifacts_dir) / "result.json").read_text()
        )
        # The result records which binding drove the run.
        assert document["binding"]["target_origin"] == origin
        assert "click_button" in document["binding"]["submit"]

    def test_second_run_needs_no_manual_picking(
        self, target: Any, tmp_path: Path
    ) -> None:
        port = target.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        binding = demo_binding(origin)
        config = make_config(port, tmp_path, mode=RunMode.AUTO, max_turns=1)
        stop = asyncio.Event()

        # Auto mode refuses to start without a binding, so reaching a turn at
        # all proves the saved locators were used rather than picked by hand.
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
                on_ready=self._auto_driver(stop),
                binding=binding,
            )
        )

        assert outcome.turns >= 1

    @staticmethod
    def _auto_driver(stop: asyncio.Event):
        async def drive(browser: Any) -> None:
            await wait_for_dock(browser)
            session = browser.__dict__.get("_session")
            del session  # the runner owns it; we simply wait for completion
            await asyncio.sleep(0.5)

        return drive


class TestAutoMode:
    def _run_auto(
        self,
        target: Any,
        tmp_path: Path,
        *,
        max_turns: int = 3,
        mode_query: str = "",
        oracles: list[Oracle] | None = None,
    ) -> Any:
        port = target.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        config = make_config(
            port, tmp_path, mode=RunMode.AUTO, max_turns=max_turns, mode_query=mode_query
        )
        stop = asyncio.Event()
        captured: dict[str, Any] = {}

        async def drive(browser: Any) -> None:
            await wait_for_dock(browser)
            # The runner started the loop; wait for it to finish on its own.
            for _ in range(600):
                if captured.get("done"):
                    break
                await asyncio.sleep(0.25)

        async def scenario() -> Any:
            task = asyncio.create_task(
                run_workbench(
                    config,
                    adapter=FakeAgentAdapter(),
                    oracles=oracles
                    or [
                        Oracle(
                            oracle_id="canary",
                            oracle_type=OracleType.REGEX,
                            pattern=CANARY_PATTERN,
                        )
                    ],
                    out=devnull(),
                    stop_event=stop,
                    on_ready=drive,
                    binding=demo_binding(origin),
                )
            )
            # Give the loop time to run, then close the session.
            await asyncio.sleep(25)
            captured["done"] = True
            stop.set()
            return await task

        return asyncio.run(scenario())

    def test_vulnerable_mode_confirms_the_canary(
        self, target: Any, tmp_path: Path
    ) -> None:
        outcome = self._run_auto(target, tmp_path, max_turns=3)

        assert outcome.status is DisclosureStatus.CONFIRMED
        assert outcome.evidence_count >= 1
        assert outcome.result["stop_reason"] == StopReason.CONFIRMED.value

    def test_safe_mode_reaches_a_bounded_non_confirmed_result(
        self, target: Any, tmp_path: Path
    ) -> None:
        outcome = self._run_auto(
            target, tmp_path, max_turns=2, mode_query="?mode=safe"
        )

        assert outcome.status is not DisclosureStatus.CONFIRMED
        assert outcome.evidence_count == 0
        assert outcome.turns <= 2

    def test_never_exceeds_max_turns(self, target: Any, tmp_path: Path) -> None:
        outcome = self._run_auto(
            target,
            tmp_path,
            max_turns=2,
            mode_query="?mode=safe",
            oracles=[
                Oracle(
                    oracle_id="never",
                    oracle_type=OracleType.FRAGMENT,
                    pattern="THIS_NEVER_APPEARS_ANYWHERE",
                )
            ],
        )

        assert outcome.turns <= 2
        assert outcome.result["stop_reason"] in {
            StopReason.MAX_TURNS.value,
            StopReason.PLANNER_STOP.value,
            StopReason.OPERATOR_STOP.value,
        }

    def test_result_records_mode_and_limits(
        self, target: Any, tmp_path: Path
    ) -> None:
        outcome = self._run_auto(target, tmp_path, max_turns=2)

        document = outcome.result
        assert document["mode"] == "auto"
        assert document["schema_version"] == 2
        assert document["limits"]["max_turns"] == 2
        assert "usage" in document
        assert document["target_data_sharing"] == "none"
        assert isinstance(document["state_transitions"], list)


class TestAutoModeGuards:
    def test_auto_send_is_a_runtime_gate_not_a_config_error(
        self, tmp_path: Path
    ) -> None:
        # Treating it as a configuration problem stopped the browser opening at
        # all, which made the interactive workflow unreachable.
        config = WorkbenchConfig(
            target_url="http://127.0.0.1:8765/",
            mode=RunMode.AUTO,
            artifacts_dir=tmp_path,
        )

        assert not any(
            "allow-auto-send" in problem for problem in config.preflight_problems()
        )
        # Headful defers to the dock; headless demands the flag.
        assert config.auto_send_authorization_problem(interactive=True) == ""
        assert "--allow-auto-send" in config.auto_send_authorization_problem(
            interactive=False
        )

    def test_auto_waives_per_send_approval_only_via_config(self) -> None:
        from stealth_prompt.workbench.config import build_workbench_config

        auto = build_workbench_config(
            target_url="http://127.0.0.1:8765/",
            mode="auto",
            allow_auto_send=True,
        )
        supervised = build_workbench_config(
            target_url="http://127.0.0.1:8765/", mode="supervised"
        )

        assert auto.safety.require_send_approval is False
        assert supervised.safety.require_send_approval is True


class TestNonFormSendButton:
    """A React/Vue-style send control: a div with a click handler, no form.

    The original implementation pressed Enter on the *button*, which does
    nothing here. Only the click-button submit strategy works.
    """

    def test_click_strategy_submits_on_a_non_form_page(
        self, target: Any, tmp_path: Path
    ) -> None:
        port = target.server_address[1]
        origin = f"http://127.0.0.1:{port}"

        binding = TargetBinding(
            target_origin=origin,
            input=BoundLocator(
                strategy=LocatorStrategy.CSS, value="#message", css_fallback="#message"
            ),
            submit_locator=BoundLocator(
                strategy=LocatorStrategy.CSS, value="#send", css_fallback="#send"
            ),
            submit_action=SubmitAction(strategy=SubmitStrategy.CLICK_BUTTON),
            response_locator=BoundLocator(
                strategy=LocatorStrategy.CSS, value=".assistant-message", pick="last"
            ),
            capture=CaptureSettings(stable_ms=1500, timeout_ms=25000),
        )

        config = WorkbenchConfig(
            target_url=f"http://127.0.0.1:{port}/js",
            browser=BrowserSettings(
                headless=True, viewport_width=1600, viewport_height=1000
            ),
            safety=SafetySettings(
                max_turns=1,
                min_turn_delay_ms=0,
                max_duration_seconds=120.0,
                require_send_approval=False,
            ),
            artifacts_dir=tmp_path / "results",
            mode=RunMode.AUTO,
            allow_auto_send=True,
        )

        stop = asyncio.Event()

        async def drive(browser: Any) -> None:
            await wait_for_dock(browser)
            # Confirm the page really has no form to submit.
            assert await browser.page.evaluate(
                "document.querySelectorAll('form').length"
            ) == 0

        async def scenario() -> Any:
            task = asyncio.create_task(
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
                    binding=binding,
                )
            )
            await asyncio.sleep(20)
            stop.set()
            return await task

        outcome = asyncio.run(scenario())

        # A reply was captured at all, which only happens if the click landed.
        assert outcome.turns >= 1
        assert outcome.result["transcript"][0]["response_sha256_short"]
