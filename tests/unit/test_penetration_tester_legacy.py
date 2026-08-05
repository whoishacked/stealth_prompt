"""Characterization tests for the legacy orchestrator.

The browser, attacker model, prompt database, operator prompt, and sleep calls
are all injected, so these tests run offline and without delays.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.penetration_tester import PenetrationTester
from tests.conftest import (
    FakeLLMClient,
    FakePromptDB,
    FakeWebAutomation,
    no_sleep,
    scripted_input,
)

CANARY = "SPCANARY7GH3KD"


def build_tester(
    config: dict[str, Any],
    *,
    llm_client: FakeLLMClient | None = None,
    web_automation: FakeWebAutomation | None = None,
    prompt_db: FakePromptDB | None = None,
    answers: list[str] | None = None,
) -> PenetrationTester:
    return PenetrationTester(
        config,
        llm_client=llm_client or FakeLLMClient(),
        web_automation=web_automation or FakeWebAutomation(),
        prompt_db=prompt_db or FakePromptDB(),
        input_fn=scripted_input(answers or []),
        sleep=no_sleep,
    )


class TestRunTest:
    def test_runs_until_max_turns_when_nothing_is_found(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        tester = build_tester(base_config)

        result = tester.run_test("system_prompt_leakage")

        assert result["status"] == "completed"
        assert result["sensitive_data_found"] is False
        assert result["total_turns"] == 3
        assert len(result["conversation_history"]) == 3

    def test_records_the_result_once(self, workdir: Path, base_config: dict[str, Any]) -> None:
        tester = build_tester(base_config)

        result = tester.run_test("system_prompt_leakage")

        assert tester.results == [result]

    def test_conversation_history_shape(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        base_config["testing"]["max_turns"] = 1
        web = FakeWebAutomation(responses=["a synthetic reply"])
        tester = build_tester(base_config, web_automation=web)

        turn = tester.run_test("system_prompt_leakage")["conversation_history"][0]

        assert turn["turn"] == 1
        assert turn["response"] == "a synthetic reply"
        assert turn["sensitive_data_found"] is False
        assert turn["from_db"] is False
        assert "payload" in turn and "check_explanation" in turn

    def test_timestamp_is_iso_formatted(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        base_config["testing"]["max_turns"] = 1
        tester = build_tester(base_config)

        result = tester.run_test("system_prompt_leakage")

        assert isinstance(result["timestamp"], str)
        assert "T" in result["timestamp"]


class TestExecutionFailures:
    def test_send_failure_is_reported_as_an_error(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        web = FakeWebAutomation(send_results=[False])
        tester = build_tester(base_config, web_automation=web)

        result = tester.run_test("system_prompt_leakage")

        assert result["status"] == "error"
        assert result["error"] == "Failed to send prompt on turn 1"
        assert result["total_turns"] == 1
        assert result["conversation_history"] == []

    def test_missing_response_is_reported_as_an_error(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        web = FakeWebAutomation(responses=[None])
        tester = build_tester(base_config, web_automation=web)

        result = tester.run_test("system_prompt_leakage")

        assert result["status"] == "error"
        assert result["error"] == "Failed to get response on turn 1"

    def test_failure_on_a_later_turn_keeps_earlier_history(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        web = FakeWebAutomation(responses=["first reply", None])
        tester = build_tester(base_config, web_automation=web)

        result = tester.run_test("system_prompt_leakage")

        assert result["status"] == "error"
        assert result["error"] == "Failed to get response on turn 2"
        assert len(result["conversation_history"]) == 1

    def test_successful_runs_carry_no_error_field(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        base_config["testing"]["max_turns"] = 1
        tester = build_tester(base_config)

        assert "error" not in tester.run_test("system_prompt_leakage")


class TestDisclosureConfirmation:
    def test_confirmed_disclosure_saves_the_chain_and_succeeds(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        base_config["testing"]["max_turns"] = 2
        llm = FakeLLMClient(
            check_results=[{"found": True, "explanation": "protected value disclosed"}]
        )
        web = FakeWebAutomation(responses=[f"the value is {CANARY}"])
        db = FakePromptDB()
        tester = build_tester(
            base_config, llm_client=llm, web_automation=web, prompt_db=db, answers=["yes", "no"]
        )

        result = tester.run_test("system_prompt_leakage")

        assert result["status"] == "success"
        assert result["sensitive_data_found"] is True
        assert tester.stop_requested is True
        assert len(db.added) == 1
        assert db.added[0]["conversation_chain"][-1]["response"] == f"the value is {CANARY}"

    def test_continuing_after_a_confirmation_keeps_testing(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        base_config["testing"]["max_turns"] = 2
        llm = FakeLLMClient(
            check_results=[{"found": True, "explanation": "disclosed"}]
        )
        tester = build_tester(base_config, llm_client=llm, answers=["yes", "yes"])

        result = tester.run_test("system_prompt_leakage")

        assert result["sensitive_data_found"] is True
        assert tester.stop_requested is False

    @pytest.mark.parametrize("answer", ["no", "continue"])
    def test_rejected_finding_continues_without_saving(
        self, workdir: Path, base_config: dict[str, Any], answer: str
    ) -> None:
        base_config["testing"]["max_turns"] = 1
        llm = FakeLLMClient(check_results=[{"found": True, "explanation": "maybe"}])
        db = FakePromptDB()
        tester = build_tester(base_config, llm_client=llm, prompt_db=db, answers=[answer])

        result = tester.run_test("system_prompt_leakage")

        assert result["status"] == "completed"
        assert result["sensitive_data_found"] is False
        assert db.added == []
        assert result["conversation_history"][0]["sensitive_data_found"] is False

    def test_invalid_answer_is_re_prompted(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        base_config["testing"]["max_turns"] = 1
        llm = FakeLLMClient(check_results=[{"found": True, "explanation": "maybe"}])
        tester = build_tester(base_config, llm_client=llm, answers=["what?", "no"])

        result = tester.run_test("system_prompt_leakage")

        assert result["sensitive_data_found"] is False


class TestPromptDatabaseIntegration:
    def test_saved_first_payload_is_reused_instead_of_generating_one(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        base_config["testing"]["max_turns"] = 1
        db = FakePromptDB(
            entries=[
                {
                    "id": "abc12345",
                    "test_type": "system_prompt_leakage",
                    "conversation_chain": [{"turn": 1, "payload": "saved opener", "response": "r"}],
                }
            ]
        )
        llm = FakeLLMClient()
        web = FakeWebAutomation()
        tester = build_tester(base_config, llm_client=llm, web_automation=web, prompt_db=db)

        tester.run_test("system_prompt_leakage")

        assert web.sent == ["saved opener"]
        assert llm.payload_calls == []

    def test_saved_chain_continues_later_turns(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        base_config["testing"]["max_turns"] = 2
        db = FakePromptDB(next_chain_prompt="saved follow-up")
        llm = FakeLLMClient()
        web = FakeWebAutomation()
        tester = build_tester(base_config, llm_client=llm, web_automation=web, prompt_db=db)

        tester.run_test("system_prompt_leakage")

        assert web.sent[1] == "saved follow-up"
        # Only the opening payload came from the model.
        assert len(llm.payload_calls) == 1

    def test_database_match_short_circuits_the_model_judge(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        base_config["testing"]["max_turns"] = 1
        db = FakePromptDB(response_matches=True)
        llm = FakeLLMClient()
        tester = build_tester(base_config, llm_client=llm, prompt_db=db, answers=["no"])

        result = tester.run_test("system_prompt_leakage")

        assert llm.check_calls == []
        assert result["conversation_history"][0]["from_db"] is True

    def test_provided_payload_takes_precedence(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        base_config["testing"]["max_turns"] = 1
        db = FakePromptDB(
            entries=[
                {
                    "id": "abc12345",
                    "test_type": "system_prompt_leakage",
                    "conversation_chain": [{"turn": 1, "payload": "saved opener", "response": "r"}],
                }
            ]
        )
        web = FakeWebAutomation()
        tester = build_tester(base_config, web_automation=web, prompt_db=db)

        tester.run_test("system_prompt_leakage", payload="caller payload")

        assert web.sent == ["caller payload"]


class TestRunAllTests:
    def test_runs_every_type_the_configured_number_of_times(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        base_config["testing"]["max_turns"] = 1
        base_config["testing"]["test_types"] = ["system_prompt_leakage", "data_extraction"]
        base_config["testing"]["tests_per_type"] = 2
        web = FakeWebAutomation()
        tester = build_tester(base_config, web_automation=web)

        results = tester.run_all_tests()

        assert len(results) == 4
        assert [r["test_type"] for r in results[:2]] == ["system_prompt_leakage"] * 2
        assert web.started == 1
        assert web.closed == 1

    def test_browser_is_closed_even_when_a_test_raises(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        class ExplodingWeb(FakeWebAutomation):
            def send_prompt(self, prompt: str, log: bool = True) -> bool:
                raise RuntimeError("driver crashed")

        web = ExplodingWeb()
        tester = build_tester(base_config, web_automation=web)

        with pytest.raises(RuntimeError, match="driver crashed"):
            tester.run_all_tests()

        assert web.closed == 1

    def test_stop_request_halts_remaining_tests(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        base_config["testing"]["max_turns"] = 1
        base_config["testing"]["test_types"] = ["system_prompt_leakage", "data_extraction"]
        llm = FakeLLMClient(check_results=[{"found": True, "explanation": "disclosed"}])
        tester = build_tester(base_config, llm_client=llm, answers=["yes", "no"])

        results = tester.run_all_tests()

        assert len(results) == 1
        assert tester.stop_requested is True


class TestOutput:
    def test_results_directory_is_created(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        build_tester(base_config)

        assert (workdir / "results").is_dir()

    def test_json_output(self, workdir: Path, base_config: dict[str, Any]) -> None:
        base_config["testing"]["max_turns"] = 1
        tester = build_tester(base_config)
        tester.run_test("system_prompt_leakage")

        tester.save_results("run")

        payload = json.loads((workdir / "results" / "run.json").read_text(encoding="utf-8"))
        assert len(payload) == 1
        assert payload[0]["test_type"] == "system_prompt_leakage"
        assert payload[0]["status"] == "completed"

    def test_txt_output_includes_the_conversation(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        base_config["testing"]["max_turns"] = 1
        base_config["output"]["format"] = "txt"
        web = FakeWebAutomation(responses=["a synthetic reply"])
        tester = build_tester(base_config, web_automation=web)
        tester.run_test("system_prompt_leakage")

        tester.save_results("run")

        text = (workdir / "results" / "run.txt").read_text(encoding="utf-8")
        assert "Status: completed" in text
        assert "a synthetic reply" in text
        assert not (workdir / "results" / "run.json").exists()

    def test_both_formats(self, workdir: Path, base_config: dict[str, Any]) -> None:
        base_config["testing"]["max_turns"] = 1
        base_config["output"]["format"] = "both"
        tester = build_tester(base_config)
        tester.run_test("system_prompt_leakage")

        tester.save_results("run")

        assert (workdir / "results" / "run.json").exists()
        assert (workdir / "results" / "run.txt").exists()

    def test_txt_output_reports_execution_errors(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        base_config["output"]["format"] = "txt"
        web = FakeWebAutomation(send_results=[False])
        tester = build_tester(base_config, web_automation=web)
        tester.run_test("system_prompt_leakage")

        tester.save_results("run")

        text = (workdir / "results" / "run.txt").read_text(encoding="utf-8")
        assert "Status: error" in text
        assert "Error: Failed to send prompt on turn 1" in text

    def test_default_filename_is_timestamped(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        tester = build_tester(base_config)

        tester.save_results()

        written = list((workdir / "results").glob("penetration_test_results_*.json"))
        assert len(written) == 1


class TestReport:
    def test_counts_by_status(self, workdir: Path, base_config: dict[str, Any]) -> None:
        tester = build_tester(base_config)
        tester.results = [
            {"test_type": "a", "status": "success", "sensitive_data_found": True, "total_turns": 2},
            {
                "test_type": "a",
                "status": "completed",
                "sensitive_data_found": False,
                "total_turns": 4,
            },
            {"test_type": "b", "status": "error", "sensitive_data_found": False, "total_turns": 1},
        ]

        report = tester.generate_report()

        assert "Total Tests: 3" in report
        assert "Sensitive Data Found: 1" in report
        assert "Completed: 2" in report
        assert "Failed: 1" in report
        assert "Average Turns per Test: 2.3" in report

    def test_error_results_count_as_failed_per_type(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        tester = build_tester(base_config)
        tester.results = [
            {"test_type": "b", "status": "error", "sensitive_data_found": False, "total_turns": 1}
        ]

        report = tester.generate_report()

        assert "  b:\n    Total: 1" in report
        assert "Failed: 1" in report

    def test_empty_report_does_not_divide_by_zero(
        self, workdir: Path, base_config: dict[str, Any]
    ) -> None:
        tester = build_tester(base_config)

        report = tester.generate_report()

        assert "Total Tests: 0" in report
        assert "Average Turns per Test: 0.0" in report
