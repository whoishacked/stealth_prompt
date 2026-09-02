from __future__ import annotations

from stealth_prompt.core.attack_state import AttackAttempt, AttackState, response_digest


def test_twenty_turn_state_keeps_early_outcomes_and_bounded_aggregates() -> None:
    state = AttackState.build(
        AttackAttempt(
            turn_id=f"turn-{turn}",
            turn=turn,
            move_id="test_boundary" if turn % 2 else "pivot",
            goal=f"goal {turn}",
            tactic=f"tactic {turn}",
            hypothesis=f"hypothesis {turn}",
            verdict="not_observed",
            failure_signature="no_relevant_signal",
        )
        for turn in range(1, 21)
    )

    document = state.to_dict()
    prompt = state.prompt_context(lambda value: value)

    assert document["total_turns"] == 20
    assert document["move_attempts"] == {"pivot": 10, "test_boundary": 10}
    assert "turn 1:" in prompt
    assert "turn 18:" in prompt
    assert len(prompt) <= 8_000


def test_exact_repeated_responses_are_structural_failures_without_storing_text() -> None:
    digest = response_digest("same target response")
    state = AttackState.build(
        [
            AttackAttempt(turn_id="one", turn=1, response_sha256=digest),
            AttackAttempt(turn_id="two", turn=2, response_sha256=digest),
        ]
    )

    assert state.repetition_streak == 1
    assert state.attempts[-1].failure_signature == "repeated_response"
    assert "same target response" not in str(state.to_dict())

    explicit = AttackState.build(
        [
            AttackAttempt(turn_id="one", turn=1, response_sha256=digest),
            AttackAttempt(
                turn_id="two",
                turn=2,
                response_sha256=digest,
                failure_signature="explicit_refusal",
            ),
        ]
    )
    assert explicit.repetition_streak == 1
    assert explicit.refusal_streak == 1
    assert explicit.attempts[-1].failure_signature == "explicit_refusal"
