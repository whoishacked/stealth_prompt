"""Tests for the installable ``stealth-prompt`` console entry point."""

from __future__ import annotations

import io

import pytest

from stealth_prompt import __version__
from stealth_prompt.cli import ExitCode, build_parser, main
from stealth_prompt.workbench.doctor import Environment
from tests.unit.workbench.test_doctor import FakeEnvironment

LOCAL_TARGET = "http://127.0.0.1:8765/chat"
REMOTE_TARGET = "https://authorized-target.example/chat"


def run_cli(argv: list[str], *, env: Environment | None = None) -> tuple[int, str, str]:
    """Run the CLI, returning ``(exit_code, stdout, stderr)``."""
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err, env=env)
    return code, out.getvalue(), err.getvalue()


def test_program_name() -> None:
    assert build_parser().prog == "stealth-prompt"


def test_version_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0
    assert "prompt-injection" in capsys.readouterr().out


def test_default_invocation_points_at_the_extension_core() -> None:
    out = io.StringIO()

    code = main([], stdout=out)

    assert code == 0
    assert "stealth-prompt serve" in out.getvalue()
    assert "stealth-prompt demo" in out.getvalue()


def test_unknown_argument_is_rejected() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--not-a-flag"])

    assert excinfo.value.code == 2


class TestDoctorCommand:
    def test_healthy_environment_exits_zero(self) -> None:
        code, out, _ = run_cli(["doctor"], env=FakeEnvironment())

        assert code == ExitCode.OK
        assert "All required components are present." in out

    def test_broken_environment_exits_with_the_environment_code(self) -> None:
        code, out, _ = run_cli(["doctor"], env=FakeEnvironment(python=(3, 9, 0)))

        assert code == ExitCode.ENVIRONMENT
        assert "Required components are missing." in out

    def test_agent_filter_is_passed_through(self) -> None:
        code, out, _ = run_cli(
            ["doctor", "--agent", "claude"], env=FakeEnvironment(on_path=())
        )

        assert code == ExitCode.ENVIRONMENT
        assert "claude" in out
        assert "codex" not in out

    def test_fake_agent_needs_no_cli_installed(self) -> None:
        code, _, _ = run_cli(
            ["doctor", "--agent", "fake"], env=FakeEnvironment(on_path=())
        )

        assert code == ExitCode.OK

    def test_rejects_an_unknown_agent(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["doctor", "--agent", "gpt-9"])

        assert excinfo.value.code == 2


class TestWorkbenchCommand:
    def test_target_is_required(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["workbench"])

        assert excinfo.value.code == 2

    def test_dry_run_validates_without_launching_anything(self) -> None:
        code, out, err = run_cli(["workbench", "--target", LOCAL_TARGET, "--dry-run"])

        assert code == ExitCode.OK
        assert "Dry run: nothing was launched." in out
        assert err == ""

    def test_dry_run_warns_when_no_oracle_is_configured(self) -> None:
        _, out, _ = run_cli(["workbench", "--target", LOCAL_TARGET, "--dry-run"])

        assert "No oracles configured" in out

    def test_oracles_are_listed_when_configured(self) -> None:
        _, out, _ = run_cli(
            [
                "workbench",
                "--target",
                LOCAL_TARGET,
                "--dry-run",
                "--expect-regex",
                r"SP_CANARY_[A-Z0-9]{12}",
            ]
        )

        assert "Oracles: regex-1" in out

    def test_invalid_oracle_regex_is_a_config_error(self) -> None:
        code, _, err = run_cli(
            ["workbench", "--target", LOCAL_TARGET, "--dry-run", "--expect-regex", "[bad"]
        )

        assert code == ExitCode.CONFIG_ERROR
        assert "does not compile" in err

    def test_plan_shows_the_authorization_notice_and_the_origin(self) -> None:
        _, out, _ = run_cli(["workbench", "--target", LOCAL_TARGET, "--dry-run"])

        assert "authorized to assess" in out
        assert "http://127.0.0.1:8765" in out

    def test_remote_target_is_refused_without_acknowledgement(self) -> None:
        code, out, err = run_cli(["workbench", "--target", REMOTE_TARGET])

        assert code == ExitCode.CONFIG_ERROR
        assert "--i-am-authorized" in err
        assert out == ""

    def test_acknowledged_remote_target_proceeds(self) -> None:
        code, out, _ = run_cli(
            ["workbench", "--target", REMOTE_TARGET, "--i-am-authorized", "--dry-run"]
        )

        assert code == ExitCode.OK
        assert "authorization_acknowledged: True" in out

    def test_malformed_target_is_a_config_error(self) -> None:
        code, _, err = run_cli(["workbench", "--target", "ftp://host/x"])

        assert code == ExitCode.CONFIG_ERROR
        assert "http or https" in err

    def test_unsafe_profile_name_is_a_config_error(self) -> None:
        code, _, err = run_cli(
            ["workbench", "--target", LOCAL_TARGET, "--profile", "../escape"]
        )

        assert code == ExitCode.CONFIG_ERROR
        assert "profile name" in err

    def test_plan_never_prints_the_broker_token(self) -> None:
        # The plan is printed to a terminal and may be pasted into a ticket.
        _, out, _ = run_cli(["workbench", "--target", LOCAL_TARGET, "--dry-run"])

        assert "token" not in out.lower()

    def test_defaults_are_the_safe_ones(self) -> None:
        _, out, _ = run_cli(["workbench", "--target", LOCAL_TARGET, "--dry-run"])

        assert "target_data_sharing: none" in out
        assert "browser_sandbox: True" in out
        assert "browser_ignore_https_errors: False" in out
        assert "require_send_approval: True" in out
        assert "browser_profile_mode: ephemeral" in out
        assert "broker_host: 127.0.0.1" in out

    def test_persistent_profile_is_warned_about(self) -> None:
        _, out, _ = run_cli(
            ["workbench", "--target", LOCAL_TARGET, "--profile", "acme-q3", "--dry-run"]
        )

        assert "WARNING" in out
        assert "persistent profile" in out

    def test_real_agent_backends_are_selectable(self) -> None:
        code, out, _ = run_cli(
            ["workbench", "--target", LOCAL_TARGET, "--agent", "claude", "--dry-run"]
        )

        assert code == ExitCode.OK
        assert "agent: claude" in out

    def test_rejects_an_unknown_agent(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(["workbench", "--target", LOCAL_TARGET, "--agent", "gpt-9"])

        assert excinfo.value.code == 2

    def test_invalid_limit_is_a_config_error(self) -> None:
        code, _, err = run_cli(
            ["workbench", "--target", LOCAL_TARGET, "--max-turns", "0"]
        )

        assert code == ExitCode.CONFIG_ERROR
        assert "max_turns" in err
