"""Environment checks for the workbench.

``stealth-prompt doctor`` answers one question: can this machine run a
workbench session, and if not, what exactly is missing? It makes no network
request, starts no browser, opens no agent session, and reads no credential.

Everything that touches the host goes through :class:`Environment`, so the
whole command is exercised offline against a fake. Child processes are spawned
with an argv list and never through a shell.

On parsing: agent CLIs are asked only for ``--version``, and only a semantic
version token is read out of the reply. The rest of the output -- banners,
colour, update notices -- is deliberately ignored rather than interpreted.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess  # noqa: S404 - argv-only, shell=False; see SystemEnvironment.run
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from importlib.util import find_spec
from pathlib import Path
from typing import Protocol

from ..agents.base import AgentKind

#: Versions whose documented interfaces the adapters target. Phase 3 and 4 pin
#: these against recorded protocol fixtures; until then they gate usage only.
MIN_PYTHON = (3, 10)
MIN_CLAUDE_VERSION = (1, 0, 0)
MIN_CODEX_VERSION = (0, 20, 0)

PROBE_TIMEOUT_S = 10.0

_VERSION_TOKEN = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class CheckStatus(str, Enum):
    """Outcome of a single environment check."""

    OK = "ok"
    WARN = "warn"
    MISSING = "missing"
    ERROR = "error"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class ProbeResult:
    """The result of running one local command."""

    found: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class DoctorCheck:
    """One named check and what to do about it."""

    name: str
    status: CheckStatus
    detail: str
    remedy: str = ""

    @property
    def blocking(self) -> bool:
        return self.status in {CheckStatus.MISSING, CheckStatus.ERROR}


@dataclass(frozen=True)
class DoctorReport:
    """The full set of checks for one invocation."""

    checks: tuple[DoctorCheck, ...]

    @property
    def ok(self) -> bool:
        return not any(check.blocking for check in self.checks)

    def render(self) -> str:
        symbols = {
            CheckStatus.OK: "ok  ",
            CheckStatus.WARN: "warn",
            CheckStatus.MISSING: "MISS",
            CheckStatus.ERROR: "ERR ",
        }
        lines = ["Stealth Prompt workbench environment", "=" * 60]
        for check in self.checks:
            lines.append(f"[{symbols[check.status]}] {check.name}: {check.detail}")
            if check.remedy and check.status is not CheckStatus.OK:
                lines.append(f"         -> {check.remedy}")
        lines.append("=" * 60)
        lines.append(
            "All required components are present."
            if self.ok
            else "Required components are missing."
        )
        return "\n".join(lines)


class Environment(Protocol):
    """Host facts the doctor needs. Faked in tests."""

    def python_version(self) -> tuple[int, int, int]: ...

    def which(self, name: str) -> str | None: ...

    def run(self, argv: Sequence[str], *, timeout_s: float = PROBE_TIMEOUT_S) -> ProbeResult: ...

    def module_available(self, module: str) -> bool: ...

    def chromium_present(self) -> bool: ...


class SystemEnvironment:
    """The real host."""

    def python_version(self) -> tuple[int, int, int]:
        info = sys.version_info
        return (info.major, info.minor, info.micro)

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def run(self, argv: Sequence[str], *, timeout_s: float = PROBE_TIMEOUT_S) -> ProbeResult:
        """Run ``argv`` with no shell and a hard timeout."""
        try:
            completed = subprocess.run(  # noqa: S603 - argv list, shell=False
                list(argv),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                shell=False,
                check=False,
            )
        except FileNotFoundError:
            return ProbeResult(found=False)
        except PermissionError:
            return ProbeResult(found=True, exit_code=None, stderr="permission denied")
        except subprocess.TimeoutExpired:
            return ProbeResult(
                found=True, exit_code=None, stderr=f"did not respond within {timeout_s:.0f}s"
            )
        return ProbeResult(
            found=True,
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    def module_available(self, module: str) -> bool:
        try:
            return find_spec(module) is not None
        except (ImportError, ValueError):
            return False

    def chromium_present(self) -> bool:
        """Look for a Playwright-managed Chromium in its documented location."""
        for directory in _playwright_browser_dirs():
            if directory.is_dir() and any(directory.glob("chromium-*")):
                return True
        return False


def _playwright_browser_dirs() -> tuple[Path, ...]:
    """Documented Playwright browser cache locations for this platform."""
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override and override != "0":
        return (Path(override),)

    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        return (home / "Library" / "Caches" / "ms-playwright",)
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else home / "AppData" / "Local"
        return (base / "ms-playwright",)
    return (home / ".cache" / "ms-playwright",)


def parse_version(text: str) -> tuple[int, int, int] | None:
    """Extract the first semantic version token from ``text``.

    Only the version is read. Surrounding banners, colour codes, and update
    notices are ignored rather than parsed, because none of that is a
    documented interface.
    """
    match = _VERSION_TOKEN.search(text)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _format_version(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def _check_python(env: Environment) -> DoctorCheck:
    version = env.python_version()
    if version[:2] < MIN_PYTHON:
        return DoctorCheck(
            name="python",
            status=CheckStatus.ERROR,
            detail=f"{_format_version(version)} is below the supported minimum",
            remedy=f"use Python {_format_version((*MIN_PYTHON, 0))} or newer",
        )
    return DoctorCheck(
        name="python", status=CheckStatus.OK, detail=_format_version(version)
    )


def _check_playwright(env: Environment) -> tuple[DoctorCheck, DoctorCheck]:
    if not env.module_available("playwright"):
        missing = DoctorCheck(
            name="playwright",
            status=CheckStatus.MISSING,
            detail="the playwright package is not installed",
            remedy='pip install "stealth-prompt[workbench]"',
        )
        skipped = DoctorCheck(
            name="chromium",
            status=CheckStatus.MISSING,
            detail="not checked because playwright is missing",
            remedy="install playwright first, then: python -m playwright install chromium",
        )
        return missing, skipped

    package = DoctorCheck(
        name="playwright", status=CheckStatus.OK, detail="package importable"
    )
    if not env.chromium_present():
        return package, DoctorCheck(
            name="chromium",
            status=CheckStatus.MISSING,
            detail="no Playwright-managed Chromium found",
            remedy="python -m playwright install chromium",
        )
    return package, DoctorCheck(
        name="chromium", status=CheckStatus.OK, detail="bundled Chromium present"
    )


def _check_agent_cli(
    env: Environment,
    *,
    name: str,
    executable: str,
    minimum: tuple[int, int, int],
    install_hint: str,
) -> DoctorCheck:
    location = env.which(executable)
    if location is None:
        # Fall back to the registry's known install locations, so doctor and
        # the runtime agree about what is installed. Codex ships inside the
        # ChatGPT desktop app and is often not on PATH.
        from ..agents.registry import ProviderKind, resolve_executable

        kind = {"claude": ProviderKind.CLAUDE, "codex": ProviderKind.CODEX}.get(name)
        if kind is not None:
            location = resolve_executable(kind)
            if location is not None:
                executable = location
    if location is None:
        return DoctorCheck(
            name=name,
            status=CheckStatus.MISSING,
            detail=f"{executable!r} is not on PATH",
            remedy=install_hint,
        )

    probe = env.run([executable, "--version"])
    if not probe.found:
        return DoctorCheck(
            name=name,
            status=CheckStatus.MISSING,
            detail=f"{executable!r} disappeared between lookup and execution",
            remedy=install_hint,
        )
    if probe.exit_code != 0:
        detail = probe.stderr.strip() or f"exit code {probe.exit_code}"
        return DoctorCheck(
            name=name,
            status=CheckStatus.ERROR,
            detail=f"{executable} --version failed: {detail}",
            remedy=install_hint,
        )

    version = parse_version(f"{probe.stdout}\n{probe.stderr}")
    if version is None:
        return DoctorCheck(
            name=name,
            status=CheckStatus.WARN,
            detail="version could not be read from --version output",
            remedy="the adapter will re-check the protocol version at session start",
        )
    if version < minimum:
        return DoctorCheck(
            name=name,
            status=CheckStatus.ERROR,
            detail=f"version {_format_version(version)} is below the required "
            f"{_format_version(minimum)}",
            remedy=install_hint,
        )
    return DoctorCheck(
        name=name,
        status=CheckStatus.OK,
        detail=f"{_format_version(version)} at {location}",
    )


def run_doctor(
    env: Environment | None = None, *, agent: AgentKind | None = None
) -> DoctorReport:
    """Run the environment checks.

    Args:
        env: Host access seam. Defaults to the real system.
        agent: Check only this backend. ``None`` checks every real backend, and
            reports a missing one as a warning rather than a failure, because a
            machine only needs the agent it intends to use.
    """
    environment = env if env is not None else SystemEnvironment()

    checks: list[DoctorCheck] = [_check_python(environment)]
    checks.extend(_check_playwright(environment))

    if agent is AgentKind.FAKE:
        checks.append(
            DoctorCheck(
                name="fake",
                status=CheckStatus.OK,
                detail="built-in deterministic backend; nothing to install",
            )
        )
    elif agent is not None:
        # Probe only the requested backend. Running the other agent's binary
        # would spawn a process the operator did not ask for.
        checks.append(_probe_agent(environment, agent))
    else:
        # No specific agent requested: report on both, but do not fail the run
        # for a backend the operator may not intend to use.
        checks.extend(
            _downgrade_to_warning(_probe_agent(environment, kind))
            for kind in (AgentKind.CLAUDE, AgentKind.CODEX)
        )

    return DoctorReport(checks=tuple(checks))


#: Executable name, minimum version, and install hint per real backend.
_AGENT_PROBES: dict[AgentKind, tuple[str, tuple[int, int, int], str]] = {
    AgentKind.CLAUDE: (
        "claude",
        MIN_CLAUDE_VERSION,
        "install Claude Code: https://claude.com/claude-code",
    ),
    AgentKind.CODEX: (
        "codex",
        MIN_CODEX_VERSION,
        "install the Codex CLI and ensure `codex` is on PATH",
    ),
}


def _probe_agent(env: Environment, kind: AgentKind) -> DoctorCheck:
    executable, minimum, hint = _AGENT_PROBES[kind]
    return _check_agent_cli(
        env,
        name=kind.value,
        executable=executable,
        minimum=minimum,
        install_hint=hint,
    )


def _downgrade_to_warning(check: DoctorCheck) -> DoctorCheck:
    if check.status is not CheckStatus.MISSING:
        return check
    return DoctorCheck(
        name=check.name,
        status=CheckStatus.WARN,
        detail=check.detail,
        remedy=check.remedy,
    )
