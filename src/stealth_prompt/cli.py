"""Console entry point for Stealth Prompt.

Two commands exist today:

``doctor``
    Report whether this machine can run a workbench session. Contacts nothing.

``workbench``
    Validate and describe a browser workbench session. Phase 1 stops before
    launching Chromium; the broker and extension arrive in phase 2.

Invoking the command with no arguments still prints the version and points at
the legacy Selenium runner, which remains the supported path until its
replacement is tested. The scenario-driven ``validate``/``run`` commands are
tracked in ``docs/migration-plan.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from enum import IntEnum
from pathlib import Path
from typing import TextIO

from . import __version__
from .agents.base import DEFAULT_TIMEOUT_MS, AgentKind, AgentUnavailableError
from .agents.registry import PROVIDERS, ProviderKind, check_health, parse_provider
from .core.server import DEFAULT_PORT as CORE_DEFAULT_PORT
from .oracles import DisclosureStatus, Oracle, OracleType
from .workbench.binding import BindingError, BindingStore
from .workbench.browser import BrowserUnavailableError
from .workbench.config import (
    RunMode,
    TargetDataSharing,
    WorkbenchConfig,
    WorkbenchConfigError,
    build_workbench_config,
)
from .workbench.doctor import Environment, run_doctor
from .workbench.runner import run_workbench

LEGACY_RUNNER_HINT = (
    "The scenario-driven commands are not implemented yet.\n"
    "Run the legacy Selenium tester from a source checkout with:\n"
    "\n"
    "    python main.py --config config.yaml\n"
    "\n"
    "See docs/migration-plan.md for the milestone that adds `validate` and `run`."
)

AUTHORIZATION_NOTICE = (
    "Test only systems you own or are explicitly authorized to assess."
)


class ExitCode(IntEnum):
    """Documented process exit codes."""

    OK = 0
    CONFIG_ERROR = 1
    USAGE = 2  # argparse reserves this
    DISCLOSURE_FOUND = 3  # a finding, not an execution failure
    ENVIRONMENT = 4


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the ``stealth-prompt`` command."""
    parser = argparse.ArgumentParser(
        prog="stealth-prompt",
        description=(
            "Black-box prompt-injection testing for authorized AI-enabled web "
            "applications and HTTP APIs."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=LEGACY_RUNNER_HINT,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"stealth-prompt {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    _add_serve_parser(subparsers)
    _add_demo_parser(subparsers)
    _add_doctor_parser(subparsers)
    _add_workbench_parser(subparsers)
    return parser


def _add_serve_parser(subparsers: argparse._SubParsersAction) -> None:
    serve = subparsers.add_parser(
        "serve",
        help="run the local Core for the browser extension",
        description=(
            "Start the local Core the Stealth Prompt browser extension connects "
            "to. Binds loopback only and never opens a browser."
        ),
    )
    serve.add_argument(
        "--port",
        type=int,
        default=CORE_DEFAULT_PORT,
        help="loopback port to listen on (default: %(default)s)",
    )
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        choices=["127.0.0.1", "::1"],
        help="loopback address to bind (default: %(default)s)",
    )
    serve.add_argument(
        "--artifacts-dir",
        default="results",
        metavar="DIR",
        help="where session evidence is written (default: %(default)s)",
    )
    serve.add_argument(
        "--expect-regex",
        action="append",
        default=[],
        metavar="PATTERN",
        dest="expect_regexes",
        help=(
            "deterministic disclosure check; a match is the only thing that can "
            "produce a 'confirmed' verdict. Repeatable."
        ),
    )


def _add_demo_parser(subparsers: argparse._SubParsersAction) -> None:
    demo = subparsers.add_parser(
        "demo",
        help="start the local demo target and the Core together",
        description=(
            "Start the intentionally vulnerable local demo target and the local "
            "Core in one step, with the demo's synthetic canary already "
            "configured as a deterministic check. Loopback only; never opens a "
            "browser and never contacts an external service."
        ),
    )
    demo.add_argument(
        "--port",
        type=int,
        default=CORE_DEFAULT_PORT,
        help="loopback port for the Core (default: %(default)s)",
    )
    demo.add_argument(
        "--target-port",
        type=int,
        default=8765,
        help="loopback port for the demo target (default: %(default)s)",
    )
    demo.add_argument(
        "--artifacts-dir",
        default="results",
        metavar="DIR",
        help="where session evidence is written (default: %(default)s)",
    )


def run_demo_command(args: argparse.Namespace, *, out: TextIO, err: TextIO) -> int:
    """Start the demo target and the Core, then wait.

    This is the five-minute first-success path, so it removes every step the
    operator would otherwise have to get right: the target, the Core, and the
    deterministic check that makes the demo finding `confirmed` rather than a
    model's opinion all come up together and already agree.
    """
    import importlib.util
    import re as _re

    from .core.server import CoreServer

    demo_path = Path(__file__).resolve().parents[2] / "examples" / "local-demo" / "server.py"
    if not demo_path.is_file():
        print(
            f"Configuration error: the demo target is not installed at {demo_path}.",
            file=err,
        )
        return int(ExitCode.CONFIG_ERROR)

    spec = importlib.util.spec_from_file_location("stealth_prompt_demo", demo_path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        print("Configuration error: the demo target could not be loaded.", file=err)
        return int(ExitCode.CONFIG_ERROR)
    demo_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo_module)

    canary = str(demo_module.CANARY)
    # The demo's canary is the deterministic check. Escaping it keeps this a
    # literal match rather than an accidental pattern.
    pattern = _re.escape(canary)

    try:
        target = demo_module.serve(port=args.target_port, verbose=False)
    except OSError as exc:
        print(f"Could not start the demo target: {exc}", file=err)
        return int(ExitCode.CONFIG_ERROR)
    target_port = target.server_address[1]

    server = CoreServer(
        host="127.0.0.1",
        port=args.port,
        artifacts_root=Path(args.artifacts_dir),
        oracle_patterns=(pattern,),
    )

    async def _serve() -> None:
        port = await server.start()
        code = server.pairing.start_pairing()
        print("Stealth Prompt guided demo", file=out)
        print("", file=out)
        print(f"1. Demo target:  http://127.0.0.1:{target_port}/", file=out)
        print(f"2. Local Core:   127.0.0.1:{port}", file=out)
        print(f"3. Pairing code: {code}", file=out)
        print("", file=out)
        print("In the browser:", file=out)
        print(f"  a. Open http://127.0.0.1:{target_port}/ and sign in if asked.", file=out)
        print("  b. Click the Stealth Prompt toolbar icon to open the Side Panel.", file=out)
        print("  c. Enter the pairing code above, then choose 'Use current tab'.", file=out)
        print("  d. Press 'Detect elements' and accept the suggested roles.", file=out)
        print("  e. Keep the Fake provider and press Start. No typing needed.", file=out)
        print("", file=out)
        print(f"Deterministic check: the demo canary {canary}", file=out)
        print(
            "A disclosure of that exact value is what makes the finding "
            "'confirmed' rather than a model opinion.",
            file=out,
        )
        print("", file=out)
        print("If something does not work:", file=out)
        print(
            "  - permission refused: reopen the panel from the toolbar icon on "
            "the target tab, then allow access for this site.",
            file=out,
        )
        print(
            "  - pairing rejected: the code expires; stop with Ctrl-C and start "
            "the demo again for a fresh one.",
            file=out,
        )
        print(
            "  - binding needs review: press 'Re-check', or 'Detect elements' "
            "and accept the roles again.",
            file=out,
        )
        print("", file=out)
        print("Press Ctrl-C to stop both.", file=out)
        out.flush()
        try:
            await asyncio.Event().wait()
        finally:
            await server.stop()

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        print("\nDemo stopped.", file=out)
    finally:
        target.shutdown()
        target.server_close()
    return int(ExitCode.OK)


def _add_doctor_parser(subparsers: argparse._SubParsersAction) -> None:
    doctor = subparsers.add_parser(
        "doctor",
        help="check whether this machine can run a workbench session",
        description=(
            "Report the state of the local workbench prerequisites. Makes no "
            "network request, starts no browser, and opens no agent session."
        ),
    )
    doctor.add_argument(
        "--agent",
        choices=[kind.value for kind in AgentKind],
        default=None,
        help="check only this agent backend (default: report on all of them)",
    )


def _add_workbench_parser(subparsers: argparse._SubParsersAction) -> None:
    workbench = subparsers.add_parser(
        "workbench",
        help="run an operator-driven browser testing session",
        description=(
            "Launch an isolated Chromium against one authorized target with an "
            "assistant dock backed by a local coding-agent CLI. "
            + AUTHORIZATION_NOTICE
        ),
    )
    workbench.add_argument(
        "--target",
        required=True,
        metavar="URL",
        help="the authorized target chat application (http or https)",
    )
    workbench.add_argument(
        "--provider",
        "--agent-backend",
        dest="provider",
        choices=[kind.value for kind in ProviderKind],
        default=None,
        help="backend that authors payloads (default: fake)",
    )
    workbench.add_argument(
        "--agent",
        choices=[kind.value for kind in AgentKind],
        default=AgentKind.FAKE.value,
        help="backward-compatible alias for --provider",
    )
    workbench.add_argument(
        "--model",
        default=None,
        metavar="NAME",
        help="model to request; omit to use the backend default",
    )
    workbench.add_argument(
        "--no-ui-configuration",
        action="store_true",
        help=(
            "refuse configuration changes from the dock; the command line "
            "becomes the only source of truth"
        ),
    )
    workbench.add_argument(
        "--profile",
        default=None,
        metavar="NAME",
        help=(
            "engagement-specific persistent browser profile; omit for an "
            "isolated temporary profile that is discarded at exit"
        ),
    )
    workbench.add_argument(
        "--headless",
        action="store_true",
        help="run Chromium headless (the dock is not visible)",
    )
    workbench.add_argument(
        "--i-am-authorized",
        action="store_true",
        dest="authorized",
        help="confirm written permission to test a non-loopback target",
    )
    workbench.add_argument(
        "--scope-note",
        default="",
        metavar="TEXT",
        help="a short, non-secret note recorded with the session",
    )
    workbench.add_argument(
        "--target-data-sharing",
        choices=[mode.value for mode in TargetDataSharing],
        default=TargetDataSharing.NONE.value,
        help=(
            "whether target responses may be sent to the agent provider "
            "(default: %(default)s)"
        ),
    )
    workbench.add_argument(
        "--artifacts-dir",
        default="results",
        metavar="DIR",
        help="where session artifacts are written (default: %(default)s)",
    )
    workbench.add_argument(
        "--timeout-ms",
        type=int,
        default=DEFAULT_TIMEOUT_MS,
        help="per-turn agent timeout in milliseconds (default: %(default)s)",
    )
    workbench.add_argument(
        "--max-turns",
        type=int,
        default=20,
        help="maximum conversation turns (default: %(default)s)",
    )
    workbench.add_argument(
        "--max-cost-usd",
        type=float,
        default=None,
        help="stop the session once reported agent cost reaches this amount",
    )
    workbench.add_argument(
        "--expect-fragment",
        action="append",
        default=[],
        metavar="TEXT",
        dest="expect_fragments",
        help=(
            "protected value whose appearance in a reply confirms disclosure; "
            "repeatable"
        ),
    )
    workbench.add_argument(
        "--expect-regex",
        action="append",
        default=[],
        metavar="PATTERN",
        dest="expect_regexes",
        help="regular expression whose match confirms disclosure; repeatable",
    )
    workbench.add_argument(
        "--mode",
        # Both spellings: the enum value uses an underscore, but a hyphen is
        # what an operator types.
        choices=sorted({m.value for m in RunMode} | {"payload-only"}),
        default=RunMode.MANUAL.value,
        help=(
            "manual: pick and approve every step; supervised: automated except "
            "the send; auto: the whole bounded loop (default: %(default)s)"
        ),
    )
    workbench.add_argument(
        "--allow-auto-send",
        action="store_true",
        help="required by --mode auto: confirms payloads may be sent unattended",
    )
    workbench.add_argument(
        "--binding",
        default=None,
        metavar="NAME|PATH",
        help="engagement binding name, or a path to a binding file",
    )
    workbench.add_argument(
        "--objective",
        default=None,
        metavar="TEXT",
        help="what the automated run is trying to establish",
    )
    workbench.add_argument(
        "--objective-file",
        default=None,
        metavar="FILE",
        help="read the objective from a file",
    )
    workbench.add_argument(
        "--max-duration-seconds",
        type=float,
        default=900.0,
        help="wall-clock ceiling for the whole run (default: %(default)s)",
    )
    workbench.add_argument(
        "--min-turn-delay-ms",
        type=int,
        default=1000,
        help="conservative delay between turns (default: %(default)s)",
    )
    workbench.add_argument(
        "--max-repeated-payloads",
        type=int,
        default=1,
        help="stop after this many repeated payloads (default: %(default)s)",
    )
    workbench.add_argument(
        "--max-repeated-responses",
        type=int,
        default=3,
        help="stop after this many near-identical replies (default: %(default)s)",
    )
    workbench.add_argument(
        "--max-consecutive-refusals",
        type=int,
        default=4,
        help="stop after this many refusals in a row (default: %(default)s)",
    )
    workbench.add_argument(
        "--no-store-transcript",
        action="store_true",
        help="record digests and evidence but not payload/response text",
    )
    workbench.add_argument(
        "--yes",
        action="store_true",
        help="skip the interactive start confirmation (for scripted use)",
    )
    workbench.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the session plan without launching anything",
    )


def run_serve_command(
    args: argparse.Namespace, *, out: TextIO, err: TextIO
) -> int:
    """Run the local Core until interrupted. Never opens a browser."""
    import re as _re

    from .core.server import CoreServer

    for pattern in args.expect_regexes:
        try:
            _re.compile(pattern)
        except _re.error as exc:
            print(f"Configuration error: {pattern!r} does not compile: {exc}",
                  file=err)
            return int(ExitCode.CONFIG_ERROR)

    server = CoreServer(
        host=args.host,
        port=args.port,
        artifacts_root=Path(args.artifacts_dir),
        oracle_patterns=tuple(args.expect_regexes),
    )

    async def _serve() -> None:
        port = await server.start()
        code = server.pairing.start_pairing()
        print("Stealth Prompt Core", file=out)
        print(f"Listening on {args.host}:{port}", file=out)
        print(f"Pairing code: {code}", file=out)
        if args.expect_regexes:
            print(
                f"Deterministic checks: {len(args.expect_regexes)} configured",
                file=out,
            )
        else:
            print(
                "No deterministic checks configured: findings stay 'potential' "
                "unless you confirm them. Add --expect-regex to enable "
                "'confirmed'.",
                file=out,
            )
        print("Open the Stealth Prompt browser extension to connect.", file=out)
        print("Press Ctrl-C to stop.", file=out)
        out.flush()
        try:
            await asyncio.Event().wait()
        finally:
            await server.stop()

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        print("\nCore stopped.", file=out)
    return int(ExitCode.OK)


def run_doctor_command(
    args: argparse.Namespace,
    *,
    out: TextIO,
    env: Environment | None = None,
) -> int:
    """Execute ``stealth-prompt doctor``."""
    agent = AgentKind(args.agent) if args.agent else None
    report = run_doctor(env, agent=agent)
    print(report.render(), file=out)
    return int(ExitCode.OK if report.ok else ExitCode.ENVIRONMENT)


def _render_plan(config: WorkbenchConfig, binding_summary: str = "") -> str:
    """The run plan an operator confirms before anything is launched."""
    from .workbench.config import RunMode, TargetDataSharing

    provider = parse_provider(config.agent.provider)
    spec = PROVIDERS[provider]
    health = check_health(provider)

    adaptive = config.safety.target_data_sharing is not TargetDataSharing.NONE
    planning = "adaptive (AI plans from target replies)" if adaptive else (
        "static payload sequence (sharing=none, so replies are never shared)"
    )
    if config.mode is RunMode.PAYLOAD_ONLY:
        planning = "adaptive" if adaptive else "objective-only (no target replies shared)"

    mutations = (
        "NONE (payload-only)"
        if config.mode is RunMode.PAYLOAD_ONLY
        else "fill/submit allowed"
    )
    lines = [
        "Workbench session plan",
        "=" * 60,
        f"  backend:            {spec.label} ({provider.value})",
        f"  model requested:    {config.agent.model or spec.default_model_label}",
        "  effective model:    reported by the backend when the session starts",
        f"  external provider:  {'YES - prompts leave this machine' if spec.external else 'no'}",
        f"  installed:          {health.installed}",
        f"  authenticated:      {health.authenticated}",
        f"  mode:               {config.mode.value}",
        f"  planning:           {planning}",
        f"  sharing policy:     {config.safety.target_data_sharing.value}",
        f"  page mutations:     {mutations}",
        f"  binding:            {binding_summary or 'not saved yet'}",
        "-" * 60,
    ]
    for key, value in config.describe().items():
        lines.append(f"  {key}: {value}")
    warnings = config.warnings()
    if warnings:
        lines.append("-" * 60)
        for warning in warnings:
            lines.append(f"  WARNING: {warning}")
    lines.append("=" * 60)
    return "\n".join(lines)


def run_workbench_command(
    args: argparse.Namespace,
    *,
    out: TextIO,
    err: TextIO,
) -> int:
    """Execute ``stealth-prompt workbench``."""
    try:
        config = build_workbench_config(
            target_url=args.target,
            agent=args.agent,
            profile=args.profile,
            headless=args.headless,
            authorized=args.authorized,
            scope_note=args.scope_note,
            target_data_sharing=args.target_data_sharing,
            artifacts_dir=Path(args.artifacts_dir),
            timeout_ms=args.timeout_ms,
            max_turns=args.max_turns,
            max_cost_usd=args.max_cost_usd,
            mode=args.mode,
            allow_auto_send=args.allow_auto_send,
            provider=args.provider,
            model=args.model,
            allow_ui_configuration=not args.no_ui_configuration,
            binding_name=_binding_name(args.binding),
            objective=_read_objective(args),
            max_duration_seconds=args.max_duration_seconds,
            min_turn_delay_ms=args.min_turn_delay_ms,
            max_repeated_payloads=args.max_repeated_payloads,
            max_repeated_responses=args.max_repeated_responses,
            max_consecutive_refusals=args.max_consecutive_refusals,
            store_transcript=not args.no_store_transcript,
        )
    except WorkbenchConfigError as exc:
        print(f"Configuration error: {exc}", file=err)
        return int(ExitCode.CONFIG_ERROR)

    problems = config.preflight_problems()
    if problems:
        for problem in problems:
            print(f"Cannot start: {problem}", file=err)
        return int(ExitCode.CONFIG_ERROR)

    try:
        oracles = build_oracles(
            fragments=args.expect_fragments, regexes=args.expect_regexes
        )
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=err)
        return int(ExitCode.CONFIG_ERROR)

    print(AUTHORIZATION_NOTICE, file=out)

    try:
        binding, binding_source = load_binding_for(args, config)
    except BindingError as exc:
        print(f"Configuration error: {exc}", file=err)
        return int(ExitCode.CONFIG_ERROR)

    print(_render_plan(config, binding.describe() if binding else ""), file=out)
    if oracles:
        print(f"Oracles: {', '.join(o.oracle_id for o in oracles)}", file=out)
    else:
        print(
            "No oracles configured: results will be inconclusive. Add "
            "--expect-regex or --expect-fragment to confirm a disclosure.",
            file=out,
        )

    if binding is not None:
        print(f"Loaded target binding from {binding_source}", file=out)
        print(f"  {binding.describe()}", file=out)
    else:
        print(
            "No saved target binding: pick the input, send, and reply elements "
            "in the dock, then use \"Save target setup\".",
            file=out,
        )

    needs_setup = (
        config.mode in {RunMode.AUTO, RunMode.SUPERVISED} and binding is None
    )
    if needs_setup:
        # Deliberately not an error. Requiring a separate manual run to create
        # the binding, then a relaunch to use it, was the workflow this release
        # exists to remove: pick the elements in the dock, save, and start in
        # this same process.
        print(
            f"{config.mode.value} mode needs a target binding. Pick the input, "
            "send, and reply elements in the dock, save the setup, then press "
            "Start -- no relaunch needed.",
            file=out,
        )

    if args.dry_run:
        if config.mode is RunMode.AUTO:
            # A dry run cannot send, so it never needs send authorization. It
            # does say which confirmation a real run would need.
            print(
                "\nA real run in auto mode needs confirmation: press Start in "
                "the dock (headful), or pass --allow-auto-send (headless).",
                file=out,
            )
        print("\nDry run: nothing was launched.", file=out)
        return int(ExitCode.OK)

    # Headless has no operator to press Start, so the authorization must be on
    # the command line. Headful defers it to the dock.
    blocked = config.auto_send_authorization_problem(
        interactive=not config.browser.headless
    )
    if blocked:
        print(f"Cannot start: {blocked}", file=err)
        return int(ExitCode.CONFIG_ERROR)

    # When a binding already exists the loop starts by itself, so the
    # confirmation belongs here. Without one, the dock's Start button is the
    # explicit confirmation instead.
    if config.mode is RunMode.AUTO and not args.yes and not needs_setup:
        if not sys.stdin.isatty():
            print(
                "Cannot start: auto mode needs an interactive confirmation. "
                "Pass --yes for scripted use.",
                file=err,
            )
            return int(ExitCode.CONFIG_ERROR)
        print(
            f"\nAbout to run up to {config.safety.max_turns} unattended turns "
            f"against {config.target_origin}.",
            file=out,
        )
        answer = input("Type 'start' to begin: ").strip().lower()
        if answer != "start":
            print("Not started.", file=out)
            return int(ExitCode.OK)

    try:
        outcome = asyncio.run(
            run_workbench(config, oracles=oracles, out=out, binding=binding)
        )
    except BrowserUnavailableError as exc:
        print(f"Environment error: {exc}", file=err)
        return int(ExitCode.ENVIRONMENT)
    except AgentUnavailableError as exc:
        print(f"Environment error: {exc}", file=err)
        return int(ExitCode.ENVIRONMENT)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("\nInterrupted.", file=err)
        return int(ExitCode.OK)

    return int(
        ExitCode.DISCLOSURE_FOUND
        if outcome.status is DisclosureStatus.CONFIRMED
        else ExitCode.OK
    )


def _binding_name(value: str | None) -> str | None:
    """A ``--binding`` that looks like a path is not a profile name."""
    if not value:
        return None
    if any(sep in value for sep in ("/", "\\")) or value.endswith(".json"):
        return None
    return value


def _read_objective(args: argparse.Namespace) -> str | None:
    if getattr(args, "objective_file", None):
        path = Path(args.objective_file).expanduser()
        if path.is_symlink():
            raise WorkbenchConfigError(f"refusing to read symlinked {path}")
        if not path.is_file():
            raise WorkbenchConfigError(f"objective file {path} does not exist")
        return path.read_text(encoding="utf-8").strip()[:4000]
    return getattr(args, "objective", None)


def load_binding_for(args: argparse.Namespace, config: WorkbenchConfig):
    """Load a saved binding by path or by target+profile. ``None`` if absent."""
    store = BindingStore()
    value = getattr(args, "binding", None)
    if value and (any(sep in value for sep in ("/", "\\")) or value.endswith(".json")):
        return store.load_path(Path(value).expanduser()), value
    binding = store.load(config.target_url, config.binding_name)
    if binding is None:
        return None, ""
    return binding, str(store.path_for(config.target_url, config.binding_name))


def build_oracles(
    *, fragments: Sequence[str] = (), regexes: Sequence[str] = ()
) -> list[Oracle]:
    """Build deterministic oracles from the command line."""
    oracles: list[Oracle] = []
    for index, fragment in enumerate(fragments, start=1):
        oracles.append(
            Oracle(
                oracle_id=f"fragment-{index}",
                oracle_type=OracleType.FRAGMENT,
                pattern=fragment,
            )
        )
    for index, pattern in enumerate(regexes, start=1):
        oracles.append(
            Oracle(
                oracle_id=f"regex-{index}",
                oracle_type=OracleType.REGEX,
                pattern=pattern,
            )
        )
    return oracles


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    env: Environment | None = None,
) -> int:
    """Run the console entry point and return a process exit code.

    ``env`` is a seam so ``doctor`` can be exercised against a fake host.
    """
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        return run_serve_command(args, out=out, err=err)
    if args.command == "demo":
        return run_demo_command(args, out=out, err=err)
    if args.command == "doctor":
        return run_doctor_command(args, out=out, env=env)
    if args.command == "workbench":
        return run_workbench_command(args, out=out, err=err)

    print(f"stealth-prompt {__version__}", file=out)
    print(LEGACY_RUNNER_HINT, file=out)
    return int(ExitCode.OK)


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
