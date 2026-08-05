"""Scenario files: round-trip, strict parsing, and the boundaries they keep.

A scenario is shared between people and machines, so the tests below care most
about what it must *refuse* to carry and what importing one must *not* grant.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from stealth_prompt.core.assistant import (
    AssistMode,
    InteractionBinding,
    PotentialFindingAction,
    build_session,
)
from stealth_prompt.core.contracts import Objective
from stealth_prompt.core.scenario_file import (
    MAX_SCENARIO_BYTES,
    SCENARIO_KIND,
    SCENARIO_SCHEMA_VERSION,
    TOP_LEVEL_FIELDS,
    Scenario,
    ScenarioError,
    ScenarioVersionError,
    parse_scenario,
    scenario_from_session,
)
from stealth_prompt.oracles import Oracle, OracleType

ORIGIN = "http://127.0.0.1:8931"


def binding(origin: str = ORIGIN) -> InteractionBinding:
    return InteractionBinding(
        origin=origin,
        input_locator={"strategy": "css", "value": "#message", "name": None, "css_fallback": None},
        submit_locator={
            "strategy": "css",
            "value": "button[type='submit']",
            "name": None,
            "css_fallback": None,
        },
        response_locator={
            "strategy": "css",
            "value": ".assistant-message",
            "name": None,
            "css_fallback": None,
        },
    )


def sample() -> Scenario:
    return Scenario(
        name="Canary disclosure on the local demo",
        description="Checks whether the demo assistant reveals its canary.",
        objective=Objective.INSTRUCTION_DISCLOSURE,
        provider="fake",
        requested_model="",
        target_origin=ORIGIN,
        binding=binding(),
        scorers=(
            Oracle(
                oracle_id="canary-1",
                oracle_type=OracleType.REGEX,
                pattern=r"SP_CANARY_[A-Z0-9]{12}",
            ),
        ),
        expected={"verdict": "confirmed", "notes": "demo target discloses on request"},
    )


class TestRoundTrip:
    def test_a_scenario_survives_export_and_import(self) -> None:
        original = sample()
        restored = parse_scenario(original.to_json())

        assert restored.name == original.name
        assert restored.objective is original.objective
        assert restored.provider == original.provider
        assert restored.target_origin == original.target_origin
        assert restored.mode is original.mode
        assert restored.potential_finding_action is PotentialFindingAction.REVIEW
        assert restored.sharing == original.sharing
        assert restored.max_turns == original.max_turns
        assert restored.expected == original.expected
        assert restored.binding is not None
        assert restored.binding.summary() == original.binding.summary()
        assert [rule.oracle_id for rule in restored.scorers] == ["canary-1"]
        assert restored.scorers[0].oracle_type is OracleType.REGEX

    def test_the_serialized_form_is_stable(self) -> None:
        """A second round trip must not drift, or diffs become unreadable."""
        once = parse_scenario(sample().to_json())
        twice = parse_scenario(once.to_json())
        assert once.to_dict() == twice.to_dict()

    def test_version_one_migrates_to_review_policy(self) -> None:
        document = sample().to_dict()
        document["schema_version"] = 1
        del document["potential_finding_action"]

        restored = parse_scenario(document)

        assert restored.schema_version == SCENARIO_SCHEMA_VERSION
        assert restored.potential_finding_action is PotentialFindingAction.REVIEW

    def test_it_is_exported_from_a_session_without_evidence(self) -> None:
        session = build_session(provider="fake", objective=Objective.SENSITIVE_DATA)
        session.bind(binding())
        session.origin = ORIGIN
        scenario = scenario_from_session(session, name="From a live session")

        record = scenario.to_dict()
        assert record["objective"] == "sensitive_data_disclosure"
        assert record["target_origin"] == ORIGIN
        # No evidence structure leaks into a scenario: those keys belong to the
        # session export. `max_turns` is a limit, not a result, so the check is
        # on the top-level keys rather than on substrings.
        for absent in ("turns", "verdict", "session_id", "timeline", "evaluation"):
            assert absent not in record
        assert set(record) <= TOP_LEVEL_FIELDS


class TestStrictParsing:
    def test_an_unknown_schema_version_is_a_distinct_error(self) -> None:
        document = sample().to_dict()
        document["schema_version"] = SCENARIO_SCHEMA_VERSION + 1
        with pytest.raises(ScenarioVersionError, match="not supported"):
            parse_scenario(json.dumps(document))

    def test_an_unknown_top_level_field_is_refused(self) -> None:
        document = sample().to_dict()
        document["run_shell_after_import"] = "curl evil.example"
        with pytest.raises(ScenarioError, match="unknown fields"):
            parse_scenario(json.dumps(document))

    def test_a_foreign_document_is_refused(self) -> None:
        with pytest.raises(ScenarioError, match="not a Stealth Prompt scenario"):
            parse_scenario(json.dumps({"schema_version": 1, "kind": "something_else"}))

    def test_an_oversized_scenario_is_refused_before_parsing(self) -> None:
        with pytest.raises(ScenarioError, match="larger than"):
            parse_scenario("x" * (MAX_SCENARIO_BYTES + 1))

    def test_invalid_json_is_refused(self) -> None:
        with pytest.raises(ScenarioError, match="not valid JSON"):
            parse_scenario("{not json")

    def test_a_scenario_needs_a_name(self) -> None:
        document = sample().to_dict()
        document["name"] = "   "
        with pytest.raises(ScenarioError, match="needs a name"):
            parse_scenario(json.dumps(document))

    def test_an_out_of_range_limit_is_refused(self) -> None:
        document = sample().to_dict()
        document["limits"]["max_turns"] = 500
        with pytest.raises(ScenarioError, match="between 0 and 100"):
            parse_scenario(json.dumps(document))

    def test_an_unsupported_objective_is_refused(self) -> None:
        document = sample().to_dict()
        document["objective"] = "delete_production"
        with pytest.raises(ScenarioError, match="unsupported objective"):
            parse_scenario(json.dumps(document))

    def test_an_unsupported_sharing_policy_is_refused(self) -> None:
        document = sample().to_dict()
        document["sharing"] = "everything"
        with pytest.raises(ScenarioError, match="unsupported sharing"):
            parse_scenario(json.dumps(document))

    def test_a_bad_locator_strategy_is_refused(self) -> None:
        document = sample().to_dict()
        document["binding"]["input"]["strategy"] = "xpath"
        with pytest.raises(ScenarioError, match="binding is invalid"):
            parse_scenario(json.dumps(document))


class TestScorerConfiguration:
    def test_an_invalid_regex_is_refused_at_import(self) -> None:
        """A rule that cannot compile must fail before a run, not during one."""
        document = sample().to_dict()
        document["scorers"][0]["pattern"] = "SP_CANARY_[A-Z"
        with pytest.raises(ScenarioError, match="does not compile"):
            parse_scenario(json.dumps(document))

    def test_a_structured_scorer_without_a_field_is_refused(self) -> None:
        document = sample().to_dict()
        document["scorers"] = [
            {"scorer_id": "s1", "type": "structured", "pattern": "secret"}
        ]
        with pytest.raises(ScenarioError, match="json_field"):
            parse_scenario(json.dumps(document))

    def test_an_unknown_scorer_type_is_refused(self) -> None:
        document = sample().to_dict()
        document["scorers"][0]["type"] = "llm_judge"
        with pytest.raises(ScenarioError, match="unsupported type"):
            parse_scenario(json.dumps(document))

    def test_duplicate_scorer_ids_are_refused(self) -> None:
        document = sample().to_dict()
        document["scorers"] = [
            {"scorer_id": "dup", "type": "fragment", "pattern": "a"},
            {"scorer_id": "dup", "type": "fragment", "pattern": "b"},
        ]
        with pytest.raises(ScenarioError, match="duplicate scorer id"):
            parse_scenario(json.dumps(document))

    def test_a_structured_scorer_round_trips_with_its_field(self) -> None:
        document = sample().to_dict()
        document["scorers"] = [
            {
                "scorer_id": "s1",
                "type": "structured",
                "pattern": "SP_CANARY",
                "json_field": "data.secret",
            }
        ]
        restored = parse_scenario(json.dumps(document))
        assert restored.scorers[0].json_field == "data.secret"


class TestNoSecretsTravel:
    """A scenario is shared; a credential or a capture must never ride along."""

    @pytest.mark.parametrize(
        "key",
        [
            "api_key",
            "openai_api_key",
            "auth_token",
            "password",
            "sessionCookie",
            "authorization",
            "extra_headers",
            "captured_response",
            "transcript",
        ],
    )
    def test_a_credential_shaped_field_is_refused(self, key: str) -> None:
        document = sample().to_dict()
        document[key] = "sk-live-abcdef"
        # Refused as an unknown field or as a forbidden one; either way it is
        # rejected rather than silently dropped.
        with pytest.raises(ScenarioError):
            parse_scenario(json.dumps(document))

    def test_a_nested_credential_is_refused(self) -> None:
        document = sample().to_dict()
        document["expected"] = {"verdict": "confirmed", "api_key": "sk-live"}
        with pytest.raises(ScenarioError):
            parse_scenario(json.dumps(document))

    def test_a_deeply_nested_credential_is_refused(self) -> None:
        document = sample().to_dict()
        document["binding"]["response"]["bearer_token"] = "abc"
        with pytest.raises(ScenarioError, match="credential"):
            parse_scenario(json.dumps(document))

    def test_the_binding_submit_key_is_still_allowed(self) -> None:
        """`key` here is a keystroke, not a secret; the scan must not overreach."""
        document = sample().to_dict()
        document["binding"]["submit"]["strategy"] = "press_key"
        document["binding"]["submit"]["key"] = "Enter"
        restored = parse_scenario(json.dumps(document))
        assert restored.binding is not None
        assert restored.binding.submit_key == "Enter"


class TestImportPreview:
    def test_a_matching_origin_produces_no_mismatch_warning(self) -> None:
        preview = sample().preview(current_origin=ORIGIN)
        assert preview["origin_mismatch"] is False
        assert not any("recorded against" in w for w in preview["warnings"])

    def test_a_different_origin_warns_explicitly(self) -> None:
        preview = sample().preview(current_origin="https://production.example")
        assert preview["origin_mismatch"] is True
        warning = " ".join(preview["warnings"])
        assert ORIGIN in warning
        assert "production.example" in warning
        assert "in scope" in warning

    def test_no_selected_target_warns(self) -> None:
        preview = sample().preview(current_origin="")
        assert any("Select the authorized target tab" in w for w in preview["warnings"])

    def test_a_preview_never_promises_automatic_send(self) -> None:
        """An imported scenario must not arrive pre-authorized to mutate a page."""
        auto = Scenario(
            name="auto run",
            objective=Objective.PROMPT_INJECTION,
            provider="fake",
            target_origin=ORIGIN,
            mode=AssistMode.AUTO,
            binding=binding(),
        )
        preview = auto.preview(current_origin=ORIGIN)
        assert preview["auto_send_authorized"] is False
        assert preview["requires_revalidation"] is True
        assert any("must be authorized again" in w for w in preview["warnings"])

    def test_a_missing_binding_is_called_out(self) -> None:
        incomplete = Scenario(
            name="no binding",
            objective=Objective.PROMPT_INJECTION,
            provider="fake",
            target_origin=ORIGIN,
        )
        preview = incomplete.preview(current_origin=ORIGIN)
        assert any("selected again" in w for w in preview["warnings"])

    def test_a_preview_carries_no_secret(self) -> None:
        serialized = json.dumps(sample().preview(current_origin=ORIGIN))
        for absent in ("api_key", "token", "password", "cookie"):
            assert absent not in serialized.lower()


def test_scenario_kind_is_stable() -> None:
    """The kind string is a compatibility surface; changing it breaks imports."""
    assert SCENARIO_KIND == "stealth_prompt_scenario"
    assert sample().to_dict()["kind"] == SCENARIO_KIND


def test_parse_accepts_a_dict_as_well_as_text() -> None:
    document: dict[str, Any] = sample().to_dict()
    assert parse_scenario(document).name == sample().name
