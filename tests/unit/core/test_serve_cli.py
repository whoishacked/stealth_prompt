"""The `stealth-prompt serve` command.

`serve` starts the local Core and nothing else. In particular it must never
launch a browser: the extension-first product expects the operator to open their
own Chrome, and a command that opens one would both surprise them and undermine
the "your normal profile" premise.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import re
import socket
from pathlib import Path
from typing import Any, Literal
from unittest import mock

import pytest

from stealth_prompt.cli import ExitCode, build_parser, main, run_serve_command

REPO = Path(__file__).resolve().parents[3]

PAIRING_CODE = re.compile(r"^Pairing code: [A-Z2-9]{4}-[A-Z2-9]{4}$", re.MULTILINE)


def serve_args(tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    """Parse a real `serve` argv, so the test tracks the actual parser."""
    argv = ["serve", "--artifacts-dir", str(tmp_path / "results")]
    for key, value in overrides.items():
        flag = "--" + key.replace("_", "-")
        argv.extend([flag, str(value)])
    return build_parser().parse_args(argv)


def run_until_listening(args: argparse.Namespace) -> dict[str, Any]:
    """Run `serve`, interrupt it once it is up, and return what it printed.

    A real `asyncio.Event().wait()` is what `serve` blocks on, so the test
    delivers a `KeyboardInterrupt` the same way Ctrl-C does rather than
    replacing the server or the loop.
    """
    out, err = io.StringIO(), io.StringIO()
    real_wait = asyncio.Event.wait

    async def interrupt_once_up(self: asyncio.Event) -> Literal[True]:
        # `serve` has printed its banner by the time it awaits; stop there.
        raise KeyboardInterrupt

    asyncio.Event.wait = interrupt_once_up  # type: ignore[method-assign]
    try:
        code = run_serve_command(args, out=out, err=err)
    finally:
        asyncio.Event.wait = real_wait  # type: ignore[method-assign]
    return {"code": code, "out": out.getvalue(), "err": err.getvalue()}


class TestServe:
    def test_prints_a_pairing_code_and_exits_cleanly(self, tmp_path: Path) -> None:
        seen = run_until_listening(serve_args(tmp_path, port=0))

        assert seen["code"] == int(ExitCode.OK)
        assert "Stealth Prompt Core" in seen["out"]
        assert PAIRING_CODE.search(seen["out"]), seen["out"]
        assert "Listening on 127.0.0.1:" in seen["out"]
        # Ctrl-C is an ordinary way to stop, not an error.
        assert "Core stopped." in seen["out"]
        assert seen["err"] == ""

    def test_never_opens_a_browser(self, tmp_path: Path, monkeypatch: Any) -> None:
        """The command must not launch, or even try to find, a browser."""
        opened: list[Any] = []

        import subprocess
        import webbrowser

        monkeypatch.setattr(webbrowser, "open", lambda *a, **k: opened.append(a))
        monkeypatch.setattr(webbrowser, "open_new", lambda *a, **k: opened.append(a))
        monkeypatch.setattr(
            subprocess, "Popen", lambda *a, **k: opened.append(a)  # noqa: S603
        )
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: opened.append(a)  # noqa: S603
        )

        seen = run_until_listening(serve_args(tmp_path, port=0))

        assert seen["code"] == int(ExitCode.OK)
        assert opened == [], "serve started something external"
        # And it tells the operator to open the extension themselves.
        assert "browser extension" in seen["out"]

    def test_binds_loopback_only(self, tmp_path: Path) -> None:
        """Every listening socket must be on a loopback address.

        The suite forbids non-loopback connections, so reachability cannot be
        probed by dialling another interface. Inspecting what the server
        actually bound is the stronger check anyway: it covers every socket,
        including ones a probe from this host would not have reached.
        """

        async def scenario() -> dict[str, Any]:
            from stealth_prompt.core.server import CoreServer

            server = CoreServer(port=0, artifacts_root=tmp_path / "results")
            port = await server.start()
            try:
                bound = [
                    sock.getsockname()[0]
                    for sock in getattr(server._server, "sockets", None) or []
                ]
                # And loopback really does answer on the advertised port.
                opened = socket.create_connection(("127.0.0.1", port), timeout=5)
                opened.close()
                return {"bound": bound, "port": port}
            finally:
                await server.stop()

        seen = asyncio.run(scenario())

        assert seen["bound"], "the server reported no listening sockets"
        for host in seen["bound"]:
            assert host in {"127.0.0.1", "::1"}, f"bound to {host}, not loopback"

    def test_a_non_loopback_host_cannot_even_be_requested(self, tmp_path: Path) -> None:
        """`--host` is a closed choice, so the refusal happens before startup."""
        with pytest.raises(SystemExit) as raised:
            build_parser().parse_args(
                ["serve", "--host", "0.0.0.0"]  # noqa: S104
            )

        assert raised.value.code == 2

    def test_the_core_itself_refuses_a_non_loopback_bind(self, tmp_path: Path) -> None:
        """Defence in depth: the parser is not the only thing enforcing this."""
        from stealth_prompt.core.server import CoreServer

        with pytest.raises(ValueError, match="loopback"):
            CoreServer(host="0.0.0.0", port=0)  # noqa: S104

    def test_an_uncompilable_expectation_is_a_configuration_error(
        self, tmp_path: Path
    ) -> None:
        args = build_parser().parse_args(
            [
                "serve",
                "--artifacts-dir",
                str(tmp_path / "results"),
                "--expect-regex",
                "SP_CANARY_[",
            ]
        )
        out, err = io.StringIO(), io.StringIO()

        code = run_serve_command(args, out=out, err=err)

        assert code == int(ExitCode.CONFIG_ERROR)
        assert "does not compile" in err.getvalue()
        # Nothing was started, so nothing was announced.
        assert out.getvalue() == ""

    def test_without_expectations_it_says_findings_stay_potential(
        self, tmp_path: Path
    ) -> None:
        """An operator must not think 'potential' means the tool is broken."""
        seen = run_until_listening(serve_args(tmp_path, port=0))

        assert "No deterministic checks configured" in seen["out"]
        assert "confirmed" in seen["out"]

    def test_with_expectations_it_counts_them(self, tmp_path: Path) -> None:
        args = build_parser().parse_args(
            [
                "serve",
                "--port",
                "0",
                "--artifacts-dir",
                str(tmp_path / "results"),
                "--expect-regex",
                r"SP_CANARY_[A-Z0-9]{12}",
                "--expect-regex",
                r"INTERNAL-\d{4}",
            ]
        )

        out, err = io.StringIO(), io.StringIO()
        real_wait = asyncio.Event.wait

        async def interrupt(self: asyncio.Event) -> Literal[True]:
            raise KeyboardInterrupt

        asyncio.Event.wait = interrupt  # type: ignore[method-assign]
        try:
            code = run_serve_command(args, out=out, err=err)
        finally:
            asyncio.Event.wait = real_wait  # type: ignore[method-assign]

        assert code == int(ExitCode.OK)
        assert "Deterministic checks: 2 configured" in out.getvalue()


class TestDemoCommand:
    """The guided demo is the five-minute first-success path."""

    def test_it_is_listed_in_help(self) -> None:
        # argparse writes --help to sys.stdout directly, so the parser's own
        # formatted help is what to assert on.
        help_text = build_parser().format_help()
        assert "demo" in help_text
        assert "start the local demo target and the Core together" in help_text

    def test_it_starts_both_and_prints_the_first_success_path(self) -> None:
        """One command, and every step the operator needs is on screen."""
        out, err = io.StringIO(), io.StringIO()

        # Ctrl-C as soon as both are up, which is what the operator does after
        # reading the instructions.
        with mock.patch(
            "asyncio.Event.wait", side_effect=KeyboardInterrupt
        ):
            code = main(["demo", "--port", "0", "--target-port", "0"], stdout=out, stderr=err)

        assert code == 0
        text = out.getvalue()
        assert "Stealth Prompt guided demo" in text
        assert "Demo target:  http://127.0.0.1:" in text
        assert "Local Core:   127.0.0.1:" in text
        assert "Pairing code:" in text
        # The deterministic check is pre-wired and named.
        assert "SP_CANARY_" in text
        assert "'confirmed'" in text
        # Recovery instructions for the three things that actually go wrong.
        assert "permission refused" in text
        assert "pairing rejected" in text
        assert "binding needs review" in text
        assert "Demo stopped." in text
        assert err.getvalue() == ""

    def test_it_never_opens_a_browser(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("webbrowser.open") as opened, mock.patch(
            "asyncio.Event.wait", side_effect=KeyboardInterrupt
        ):
            main(["demo", "--port", "0", "--target-port", "0"], stdout=out, stderr=err)
        opened.assert_not_called()

    def test_the_demo_canary_is_matched_literally(self) -> None:
        """The canary is escaped, so it is an exact match rather than a pattern."""
        import importlib.util
        import re

        from stealth_prompt.oracles import Observation, Oracle, OracleType, run_scorers

        spec = importlib.util.spec_from_file_location(
            "demo_for_cli_test", REPO / "examples" / "local-demo" / "server.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        rule = Oracle(
            oracle_id="canary",
            oracle_type=OracleType.REGEX,
            pattern=re.escape(str(module.CANARY)),
        )
        _, matched = run_scorers(
            [rule], Observation.of_text(f"The code is {module.CANARY}."), turn=1
        )
        _, missed = run_scorers(
            [rule], Observation.of_text("I cannot share that."), turn=1
        )
        assert matched.value == "confirmed"
        assert missed.value == "not_detected"
