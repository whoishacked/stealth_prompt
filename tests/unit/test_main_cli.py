"""Characterization tests for the legacy ``python main.py`` entry point.

The configuration loader and the tester are injected, so no browser starts and
no provider is contacted. The single-test path is built on the real
``PenetrationTester`` because that is where the duplicate-result defect lived.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

import main
from src.penetration_tester import PenetrationTester
from tests.conftest import (
    FakeLLMClient,
    FakePromptDB,
    FakeWebAutomation,
    no_sleep,
    scripted_input,
)


class FakeConfigLoader:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config


def loader_factory(config: dict[str, Any]) -> Any:
    def _factory(path: str) -> FakeConfigLoader:
        return FakeConfigLoader(config)

    return _factory


class RecordingTester:
    """Minimal tester double for the paths that do not need the real class."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.results: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.llm_client = FakeLLMClient()
        self.web_automation = FakeWebAutomation()

    def run_test(self, test_type: str, payload: str | None = None) -> dict[str, Any]:
        self.calls.append(f"run_test:{test_type}")
        result = {"test_type": test_type, "status": "completed"}
        self.results.append(result)
        return result

    def run_all_tests(self) -> list[dict[str, Any]]:
        self.calls.append("run_all_tests")
        return self.results

    def save_results(self, filename: str | None = None) -> None:
        self.calls.append("save_results")

    def generate_report(self) -> str:
        self.calls.append("generate_report")
        return "REPORT BODY"


class TestParser:
    def test_defaults(self) -> None:
        args = main.build_parser().parse_args([])

        assert args.config == "config.yaml"
        assert args.test_type is None
        assert args.dry_run is False

    def test_documented_flags_still_parse(self) -> None:
        args = main.build_parser().parse_args(
            ["--config", "custom.yaml", "--test-type", "prompt_injection", "--dry-run"]
        )

        assert args.config == "custom.yaml"
        assert args.test_type == "prompt_injection"
        assert args.dry_run is True

    def test_help_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main.build_parser().parse_args(["--help"])

        assert excinfo.value.code == 0
        assert "--test-type" in capsys.readouterr().out


class TestDryRun:
    def test_generates_one_payload_per_configured_type(
        self, base_config: dict[str, Any]
    ) -> None:
        base_config["testing"]["test_types"] = ["system_prompt_leakage", "data_extraction"]
        tester = RecordingTester(base_config)
        out = io.StringIO()

        code = main.run(
            ["--dry-run"],
            config_loader_factory=loader_factory(base_config),
            tester_factory=lambda config: tester,
            out=out,
        )

        assert code == 0
        assert len(tester.llm_client.payload_calls) == 2
        assert tester.web_automation.started == 0
        assert "DRY RUN MODE" in out.getvalue()

    def test_test_type_narrows_the_dry_run(self, base_config: dict[str, Any]) -> None:
        base_config["testing"]["test_types"] = ["system_prompt_leakage", "data_extraction"]
        tester = RecordingTester(base_config)

        main.run(
            ["--dry-run", "--test-type", "data_extraction"],
            config_loader_factory=loader_factory(base_config),
            tester_factory=lambda config: tester,
            out=io.StringIO(),
        )

        assert [c["test_type"] for c in tester.llm_client.payload_calls] == ["data_extraction"]

    def test_dry_run_does_not_save_results(self, base_config: dict[str, Any]) -> None:
        tester = RecordingTester(base_config)

        main.run(
            ["--dry-run"],
            config_loader_factory=loader_factory(base_config),
            tester_factory=lambda config: tester,
            out=io.StringIO(),
        )

        assert "save_results" not in tester.calls


class TestSingleTest:
    def test_result_is_recorded_exactly_once(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        # Regression test: main.py used to append the result that run_test had
        # already recorded, so single-test output contained a duplicate.
        base_config["testing"]["max_turns"] = 1
        web = FakeWebAutomation()
        tester = PenetrationTester(
            base_config,
            llm_client=FakeLLMClient(),
            web_automation=web,
            prompt_db=FakePromptDB(),
            input_fn=scripted_input([]),
            sleep=no_sleep,
        )

        code = main.run(
            ["--test-type", "system_prompt_leakage"],
            config_loader_factory=loader_factory(base_config),
            tester_factory=lambda config: tester,
            out=io.StringIO(),
        )

        assert code == 0
        assert len(tester.results) == 1
        assert tester.results[0]["test_type"] == "system_prompt_leakage"

    def test_browser_is_started_and_closed(self, base_config: dict[str, Any]) -> None:
        tester = RecordingTester(base_config)

        main.run(
            ["--test-type", "system_prompt_leakage"],
            config_loader_factory=loader_factory(base_config),
            tester_factory=lambda config: tester,
            out=io.StringIO(),
        )

        assert tester.web_automation.started == 1
        assert tester.web_automation.closed == 1

    def test_browser_is_closed_when_the_test_raises(self, base_config: dict[str, Any]) -> None:
        class ExplodingTester(RecordingTester):
            def run_test(self, test_type: str, payload: str | None = None) -> dict[str, Any]:
                raise RuntimeError("driver crashed")

        tester = ExplodingTester(base_config)

        code = main.run(
            ["--test-type", "system_prompt_leakage"],
            config_loader_factory=loader_factory(base_config),
            tester_factory=lambda config: tester,
            out=io.StringIO(),
            err=io.StringIO(),
        )

        assert code == 1
        assert tester.web_automation.closed == 1


class TestFullRun:
    def test_runs_saves_and_reports(self, base_config: dict[str, Any]) -> None:
        tester = RecordingTester(base_config)
        out = io.StringIO()

        code = main.run(
            [],
            config_loader_factory=loader_factory(base_config),
            tester_factory=lambda config: tester,
            out=out,
        )

        assert code == 0
        assert tester.calls == ["run_all_tests", "save_results", "generate_report"]
        assert "REPORT BODY" in out.getvalue()


class TestErrorHandling:
    def _run_with(self, exception: BaseException, base_config: dict[str, Any]) -> tuple[int, str]:
        def failing_loader(path: str) -> Any:
            raise exception

        err = io.StringIO()
        code = main.run(
            [],
            config_loader_factory=failing_loader,
            tester_factory=lambda config: RecordingTester(base_config),
            out=io.StringIO(),
            err=err,
        )
        return code, err.getvalue()

    def test_missing_config_file(self, base_config: dict[str, Any]) -> None:
        code, err = self._run_with(FileNotFoundError("no config.yaml"), base_config)

        assert code == 1
        assert "Error: no config.yaml" in err

    def test_invalid_config(self, base_config: dict[str, Any]) -> None:
        code, err = self._run_with(ValueError("Invalid LLM provider"), base_config)

        assert code == 1
        assert "Configuration error: Invalid LLM provider" in err

    def test_unexpected_error(self, base_config: dict[str, Any]) -> None:
        code, err = self._run_with(RuntimeError("something broke"), base_config)

        assert code == 1
        assert "Unexpected error: something broke" in err

    def test_keyboard_interrupt_closes_the_browser_and_exits_zero(
        self, base_config: dict[str, Any]
    ) -> None:
        class InterruptingTester(RecordingTester):
            def run_all_tests(self) -> list[dict[str, Any]]:
                raise KeyboardInterrupt

        tester = InterruptingTester(base_config)

        code = main.run(
            [],
            config_loader_factory=loader_factory(base_config),
            tester_factory=lambda config: tester,
            out=io.StringIO(),
        )

        assert code == 0
        assert tester.web_automation.closed == 1
