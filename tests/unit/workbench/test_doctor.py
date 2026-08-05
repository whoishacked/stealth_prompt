"""Tests for ``stealth-prompt doctor``.

Every host interaction goes through :class:`FakeEnvironment`, so no real
subprocess runs and no PATH lookup escapes the test.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from stealth_prompt.agents import AgentKind
from stealth_prompt.workbench.doctor import (
    MIN_CLAUDE_VERSION,
    CheckStatus,
    DoctorCheck,
    DoctorReport,
    ProbeResult,
    SystemEnvironment,
    parse_version,
    run_doctor,
)


class FakeEnvironment:
    """A scripted host."""

    def __init__(
        self,
        *,
        python: tuple[int, int, int] = (3, 12, 0),
        on_path: Sequence[str] = ("claude", "codex"),
        versions: dict[str, str] | None = None,
        exit_codes: dict[str, int] | None = None,
        modules: Sequence[str] = ("playwright",),
        chromium: bool = True,
    ) -> None:
        self._python = python
        self._on_path = set(on_path)
        self._versions = versions or {"claude": "1.2.3", "codex": "0.44.0"}
        self._exit_codes = exit_codes or {}
        self._modules = set(modules)
        self._chromium = chromium
        self.commands: list[list[str]] = []

    def python_version(self) -> tuple[int, int, int]:
        return self._python

    def which(self, name: str) -> str | None:
        return f"/usr/local/bin/{name}" if name in self._on_path else None

    def run(self, argv: Sequence[str], *, timeout_s: float = 10.0) -> ProbeResult:
        self.commands.append(list(argv))
        executable = argv[0]
        if executable not in self._on_path:
            return ProbeResult(found=False)
        code = self._exit_codes.get(executable, 0)
        if code != 0:
            return ProbeResult(found=True, exit_code=code, stderr="boom")
        return ProbeResult(
            found=True, exit_code=0, stdout=self._versions.get(executable, "")
        )

    def module_available(self, module: str) -> bool:
        return module in self._modules

    def chromium_present(self) -> bool:
        return self._chromium


def check_named(report: DoctorReport, name: str) -> DoctorCheck:
    for check in report.checks:
        if check.name == name:
            return check
    raise AssertionError(f"no check named {name!r} in {[c.name for c in report.checks]}")


class TestParseVersion:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1.2.3", (1, 2, 3)),
            ("claude 1.2.3 (Claude Code)", (1, 2, 3)),
            ("codex-cli 0.44.0\n", (0, 44, 0)),
            ("\x1b[32m2.0.1\x1b[0m", (2, 0, 1)),
            ("v10.20.30 extra", (10, 20, 30)),
        ],
    )
    def test_reads_the_version_token(self, text: str, expected: tuple[int, int, int]) -> None:
        assert parse_version(text) == expected

    @pytest.mark.parametrize("text", ["", "no version here", "1.2", "beta"])
    def test_returns_none_when_absent(self, text: str) -> None:
        assert parse_version(text) is None

    def test_ignores_trailing_decoration_rather_than_interpreting_it(self) -> None:
        # Update notices and banners are not a documented interface.
        assert parse_version("1.0.0\n\nA new version is available!") == (1, 0, 0)


class TestPythonCheck:
    def test_supported_python_passes(self) -> None:
        report = run_doctor(FakeEnvironment(python=(3, 12, 1)))

        assert check_named(report, "python").status is CheckStatus.OK

    def test_old_python_is_an_error(self) -> None:
        report = run_doctor(FakeEnvironment(python=(3, 9, 6)))

        check = check_named(report, "python")
        assert check.status is CheckStatus.ERROR
        assert check.blocking is True
        assert report.ok is False


class TestPlaywrightChecks:
    def test_present_playwright_and_chromium_pass(self) -> None:
        report = run_doctor(FakeEnvironment())

        assert check_named(report, "playwright").status is CheckStatus.OK
        assert check_named(report, "chromium").status is CheckStatus.OK

    def test_missing_playwright_names_the_extra(self) -> None:
        report = run_doctor(FakeEnvironment(modules=()))

        check = check_named(report, "playwright")
        assert check.status is CheckStatus.MISSING
        assert "stealth-prompt[workbench]" in check.remedy

    def test_chromium_is_not_probed_without_playwright(self) -> None:
        report = run_doctor(FakeEnvironment(modules=(), chromium=False))

        check = check_named(report, "chromium")
        assert check.status is CheckStatus.MISSING
        assert "playwright is missing" in check.detail

    def test_missing_chromium_gives_the_install_command(self) -> None:
        report = run_doctor(FakeEnvironment(chromium=False))

        check = check_named(report, "chromium")
        assert check.status is CheckStatus.MISSING
        assert "playwright install chromium" in check.remedy


class TestAgentChecks:
    def test_requested_agent_is_the_only_one_reported(self) -> None:
        report = run_doctor(FakeEnvironment(), agent=AgentKind.CLAUDE)

        names = [check.name for check in report.checks]
        assert "claude" in names
        assert "codex" not in names

    def test_version_and_location_are_reported(self) -> None:
        report = run_doctor(FakeEnvironment(), agent=AgentKind.CLAUDE)

        check = check_named(report, "claude")
        assert check.status is CheckStatus.OK
        assert "1.2.3" in check.detail
        assert "/usr/local/bin/claude" in check.detail

    def test_probe_uses_an_argv_array_with_no_shell_metacharacters(self) -> None:
        env = FakeEnvironment()

        run_doctor(env, agent=AgentKind.CLAUDE)

        assert env.commands == [["claude", "--version"]]

    def test_missing_requested_agent_blocks(self) -> None:
        report = run_doctor(FakeEnvironment(on_path=()), agent=AgentKind.CLAUDE)

        check = check_named(report, "claude")
        assert check.status is CheckStatus.MISSING
        assert report.ok is False

    def test_too_old_agent_blocks_and_states_both_versions(self) -> None:
        env = FakeEnvironment(versions={"claude": "0.1.0"})

        report = run_doctor(env, agent=AgentKind.CLAUDE)

        check = check_named(report, "claude")
        assert check.status is CheckStatus.ERROR
        assert "0.1.0" in check.detail
        assert ".".join(str(p) for p in MIN_CLAUDE_VERSION) in check.detail

    def test_failing_probe_is_an_error_not_a_crash(self) -> None:
        env = FakeEnvironment(exit_codes={"claude": 1})

        report = run_doctor(env, agent=AgentKind.CLAUDE)

        assert check_named(report, "claude").status is CheckStatus.ERROR

    def test_unreadable_version_warns_rather_than_blocks(self) -> None:
        env = FakeEnvironment(versions={"claude": "unstable-build"})

        report = run_doctor(env, agent=AgentKind.CLAUDE)

        check = check_named(report, "claude")
        assert check.status is CheckStatus.WARN
        assert check.blocking is False

    def test_fake_agent_needs_nothing_installed(self) -> None:
        report = run_doctor(FakeEnvironment(on_path=()), agent=AgentKind.FAKE)

        assert check_named(report, "fake").status is CheckStatus.OK
        assert report.ok is True

    def test_unrequested_missing_agents_only_warn(self) -> None:
        # A machine only needs the agent it intends to use, so a general
        # doctor run must not fail because the other CLI is absent.
        report = run_doctor(FakeEnvironment(on_path=()))

        assert check_named(report, "claude").status is CheckStatus.WARN
        assert check_named(report, "codex").status is CheckStatus.WARN
        assert report.ok is True

    def test_both_agents_reported_when_none_requested(self) -> None:
        report = run_doctor(FakeEnvironment())

        assert check_named(report, "claude").status is CheckStatus.OK
        assert check_named(report, "codex").status is CheckStatus.OK


class TestReport:
    def test_render_includes_every_check_and_a_verdict(self) -> None:
        report = run_doctor(FakeEnvironment())

        rendered = report.render()

        for check in report.checks:
            assert check.name in rendered
        assert "All required components are present." in rendered

    def test_render_shows_remedies_for_problems(self) -> None:
        report = run_doctor(FakeEnvironment(modules=()))

        rendered = report.render()

        assert "stealth-prompt[workbench]" in rendered
        assert "Required components are missing." in rendered

    def test_ok_is_false_when_any_check_blocks(self) -> None:
        assert run_doctor(FakeEnvironment(python=(3, 9, 0))).ok is False


class TestSystemEnvironment:
    """The real environment, exercised without touching an agent CLI."""

    def test_missing_executable_reports_not_found(self) -> None:
        result = SystemEnvironment().run(["definitely-not-a-real-binary-xyz"])

        assert result.found is False

    def test_module_available_agrees_with_the_interpreter(self) -> None:
        env = SystemEnvironment()

        assert env.module_available("json") is True
        assert env.module_available("not_a_real_module_xyz") is False

    def test_python_version_matches_the_interpreter(self) -> None:
        import sys

        assert SystemEnvironment().python_version() == (
            sys.version_info.major,
            sys.version_info.minor,
            sys.version_info.micro,
        )
