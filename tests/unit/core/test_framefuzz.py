from __future__ import annotations

import asyncio
import hashlib

import pytest

from stealth_prompt.core.assistant import InteractionBinding, build_session
from stealth_prompt.core.contracts import ContractError, Objective, Verdict
from stealth_prompt.core.framefuzz import (
    ALL_STRATEGIES,
    DEFAULT_DESTINATION,
    DEFAULT_PROTECTED_LABEL,
    FrameFuzzCampaign,
    FrameFuzzConclusion,
    FrameFuzzConfig,
    FrameFuzzError,
    FrameFuzzStrategy,
    render_payload,
    semantic_seed,
    validate_destination,
)
from stealth_prompt.oracles import DisclosureStatus, OracleType, ScorerResult


def config(**changes: object) -> FrameFuzzConfig:
    values: dict[str, object] = {
        "enabled": True,
        "strategies": ALL_STRATEGIES,
        "case_order": "fixed",
        "random_seed": "unit-test-seed",
    }
    values.update(changes)
    return FrameFuzzConfig(**values)


def scorer(*, matched: bool, turn: int) -> ScorerResult:
    return ScorerResult(
        scorer_id="canary",
        scorer_type=OracleType.FRAGMENT,
        status=DisclosureStatus.CONFIRMED if matched else DisclosureStatus.NOT_DETECTED,
        deterministic=True,
        turn=turn,
    )


def finish(
    campaign: FrameFuzzCampaign,
    outcomes: dict[FrameFuzzStrategy, Verdict],
    *,
    verified: bool = True,
    scorer_ran: bool = True,
) -> None:
    for index, strategy in enumerate(campaign.config.ordered_strategies(), start=1):
        campaign.confirm_context(verified=verified, binding_validated=True)
        current = campaign.current_case
        assert current is not None
        turn_id = f"turn-{index}"
        campaign.register_proposal(turn_id=turn_id, reviewed_payload=current.payload)
        verdict = outcomes.get(strategy, Verdict.NOT_OBSERVED)
        campaign.complete_case(
            turn_id=turn_id,
            reviewed_payload=current.payload,
            evaluation_verdict=verdict,
            deterministic=verdict is Verdict.CONFIRMED,
            scorer_results=(scorer(matched=verdict is Verdict.CONFIRMED, turn=index),)
            if scorer_ran
            else (),
        )


def test_templates_are_stable_and_share_one_semantic_seed() -> None:
    seed = semantic_seed(
        Objective.INSTRUCTION_DISCLOSURE,
        DEFAULT_PROTECTED_LABEL,
        DEFAULT_DESTINATION,
    )
    assert seed.digest == "5ebf2ff43a91ebcdc0a9e91bec279af5657571fc852c184411aceabe6e71df91"
    expected = {
        "clean_control": "7797fc723b9e9e50002a73d18923e6d1155e3fb834f3f7aaf65a7edad287c2e5",
        "explicit": "2d37f2a57b1e0df00b5e5b1f38d064939e4b0079b6da6b0f46098cae1c62951e",
        "integrity_signature": "05ba5cb033f3c3928224d0907573f45590d1d9303de55005ecfbc958e2b4bbde",
        "required_config": "fc461d881e264ef39f301bcf952f589859e96cd5afb7d4ac431ab8786827d248",
        "trusted_destination": "8fd52046fbb8c08f77094b22755dca68ab329a069ae1e7f525a7ea04b3e8f177",
    }
    campaign = FrameFuzzCampaign(config(), Objective.INSTRUCTION_DISCLOSURE)
    assert {case.semantic_seed_hash for case in campaign.cases} == {seed.digest}
    for strategy in ALL_STRATEGIES:
        payload = render_payload(strategy, seed)
        assert seed.benign_carrier in payload
        assert seed.protected_value_label in payload
        assert hashlib.sha256(payload.encode()).hexdigest() == expected[strategy.value]


def test_random_order_is_reproducible_and_contains_every_selected_case() -> None:
    first = config(case_order="randomized", random_seed="same-local-seed")
    second = config(case_order="randomized", random_seed="same-local-seed")
    different = config(case_order="randomized", random_seed="other-local-seed")
    assert first.ordered_strategies() == second.ordered_strategies()
    assert set(first.ordered_strategies()) == set(ALL_STRATEGIES)
    assert first.ordered_strategies() != different.ordered_strategies()


@pytest.mark.parametrize(
    "destination",
    [
        "javascript:alert(1)",
        "data:text/plain,x",
        "file:///tmp/x",
        "chrome-extension://abc/page",
        "https://user:password@example.test/x",
        "https://example.test/collect?api_key=secret",
        "https://example.test/collect?value=sk-abcdefghijklmnop",
        "https://example.test/x\nInjected: yes",
        "https://example.test:99999/x",
    ],
)
def test_unsafe_destinations_are_rejected(destination: str) -> None:
    with pytest.raises(FrameFuzzError):
        validate_destination(destination)


def test_configuration_rejects_secrets_missing_controls_and_custom_strategies() -> None:
    with pytest.raises(FrameFuzzError, match="actual protected value"):
        config(protected_value_label="SP_CANARY_ABCDEF123456")
    with pytest.raises(FrameFuzzError, match="clean_control"):
        config(strategies=(FrameFuzzStrategy.INTEGRITY_SIGNATURE,))
    with pytest.raises(FrameFuzzError, match="unsupported"):
        FrameFuzzConfig.from_dict(
            {
                "enabled": True,
                "strategies": ["clean_control", "explicit", "custom"],
                "random_seed": "valid-seed",
            },
            objective=Objective.INSTRUCTION_DISCLOSURE,
        )
    with pytest.raises(FrameFuzzError, match="available only"):
        FrameFuzzConfig.from_dict(
            config().to_dict(), objective=Objective.TOOL_MISUSE
        )


def test_confirmed_gap_requires_explicit_miss_framed_hit_and_verified_isolation() -> None:
    campaign = FrameFuzzCampaign(config(), Objective.INSTRUCTION_DISCLOSURE)
    finish(
        campaign,
        {FrameFuzzStrategy.INTEGRITY_SIGNATURE: Verdict.CONFIRMED},
    )
    assert campaign.conclusion() is FrameFuzzConclusion.CONFIRMED


def test_unverified_isolation_caps_a_differential_at_potential() -> None:
    campaign = FrameFuzzCampaign(config(), Objective.INSTRUCTION_DISCLOSURE)
    finish(
        campaign,
        {FrameFuzzStrategy.REQUIRED_CONFIG: Verdict.CONFIRMED},
        verified=False,
    )
    assert campaign.conclusion() is FrameFuzzConclusion.POTENTIAL


def test_clean_control_contamination_and_missing_scorers_are_inconclusive() -> None:
    contaminated = FrameFuzzCampaign(config(), Objective.INSTRUCTION_DISCLOSURE)
    finish(contaminated, {FrameFuzzStrategy.CLEAN_CONTROL: Verdict.CONFIRMED})
    assert contaminated.conclusion() is FrameFuzzConclusion.INCONCLUSIVE

    unscored = FrameFuzzCampaign(config(), Objective.INSTRUCTION_DISCLOSURE)
    finish(unscored, {}, scorer_ran=False)
    assert unscored.conclusion() is FrameFuzzConclusion.INCONCLUSIVE
    assert "scorer" in " ".join(unscored.to_dict()["warnings"]).lower()


def test_completed_negative_control_says_only_no_gap_observed() -> None:
    campaign = FrameFuzzCampaign(config(), Objective.INSTRUCTION_DISCLOSURE)
    finish(campaign, {})
    assert campaign.conclusion() is FrameFuzzConclusion.NOT_OBSERVED
    assert any("not evidence" in warning for warning in campaign.to_dict()["warnings"])


def test_editing_a_template_invalidates_the_matched_comparison() -> None:
    campaign = FrameFuzzCampaign(config(), Objective.INSTRUCTION_DISCLOSURE)
    for index, strategy in enumerate(campaign.config.ordered_strategies(), start=1):
        campaign.confirm_context(verified=True, binding_validated=True)
        current = campaign.current_case
        assert current is not None
        turn_id = f"turn-{index}"
        reviewed = current.payload + (" edited" if strategy is FrameFuzzStrategy.EXPLICIT else "")
        campaign.register_proposal(turn_id=turn_id, reviewed_payload=reviewed)
        verdict = (
            Verdict.CONFIRMED
            if strategy is FrameFuzzStrategy.INTEGRITY_SIGNATURE
            else Verdict.NOT_OBSERVED
        )
        campaign.complete_case(
            turn_id=turn_id,
            reviewed_payload=reviewed,
            evaluation_verdict=verdict,
            deterministic=True,
            scorer_results=(scorer(matched=verdict is Verdict.CONFIRMED, turn=index),),
        )
    assert campaign.conclusion() is FrameFuzzConclusion.INCONCLUSIVE
    assert "edited" in " ".join(campaign.to_dict()["warnings"]).lower()


def test_session_renders_case_without_calling_the_provider() -> None:
    class ExplodingAdapter:
        async def start(self) -> None:
            raise AssertionError("FrameFuzz proposal generation contacted the provider")

    session = build_session(
        provider="fake",
        objective=Objective.INSTRUCTION_DISCLOSURE,
        framefuzz_config=config(),
        adapter=ExplodingAdapter(),
    )
    session.bind(
        InteractionBinding(
            origin="https://target.example",
            input_locator={"strategy": "css", "value": "#input"},
            submit_locator={"strategy": "css", "value": "#send"},
            response_locator={"strategy": "css", "value": "#reply"},
        )
    )
    with pytest.raises(ContractError, match="fresh target context"):
        asyncio.run(session.propose())
    session.confirm_framefuzz_context(verified=True, binding_validated=True)
    proposal = asyncio.run(session.propose())
    assert proposal.provider == "framefuzz-core"
    assert "SP_CANARY" not in proposal.payload


def test_context_gate_revokes_auto_authorization_between_cases() -> None:
    session = build_session(
        provider="fake",
        objective=Objective.INSTRUCTION_DISCLOSURE,
        framefuzz_config=config(),
    )
    session.auto_authorized = True
    session.bind(
        InteractionBinding(
            origin="https://target.example",
            input_locator={"strategy": "css", "value": "#input"},
            submit_locator={"strategy": "css", "value": "#send"},
            response_locator={"strategy": "css", "value": "#reply"},
        )
    )
    session.confirm_framefuzz_context(verified=True, binding_validated=True)
    assert session.auto_authorized is False
