"""Bounded, versioned memory for one multi-turn assessment.

The state keeps structural outcomes, not another transcript. Raw payloads and
target replies remain in report evidence; prompts receive only this compact
memory plus the latest two shared turns.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any

from .contracts import FailureSignature, Verdict

ATTACK_STATE_SCHEMA_VERSION = 1
MAX_ATTEMPTS = 100
MAX_OBSERVATIONS = 24
MAX_HYPOTHESES = 16
MAX_CONTEXT_CHARS = 8_000


def _bounded(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


@dataclass(frozen=True)
class AttackAttempt:
    """Sanitized planning metadata for one turn."""

    turn_id: str
    turn: int
    strategy_id: str = "cold_start"
    move_id: str = "adaptive_probe"
    goal: str = ""
    tactic: str = ""
    hypothesis: str = ""
    pivot_reason: str = ""
    verdict: str = ""
    failure_signature: str = ""
    evaluation_summary: str = ""
    observed_signals: tuple[str, ...] = ()
    response_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "turn": self.turn,
            "strategy_id": self.strategy_id,
            "move_id": self.move_id,
            "goal": self.goal,
            "tactic": self.tactic,
            "hypothesis": self.hypothesis,
            "pivot_reason": self.pivot_reason,
            "verdict": self.verdict,
            "failure_signature": self.failure_signature,
            "evaluation_summary": self.evaluation_summary,
            "observed_signals": list(self.observed_signals),
            "response_sha256": self.response_sha256,
        }


@dataclass(frozen=True)
class AttackState:
    """A reproducible view derived from the session's turns."""

    attempts: tuple[AttackAttempt, ...] = ()
    total_turns: int = 0
    refusal_streak: int = 0
    repetition_streak: int = 0
    schema_version: int = ATTACK_STATE_SCHEMA_VERSION

    @classmethod
    def build(cls, attempts: Iterable[AttackAttempt]) -> AttackState:
        records = list(attempts)
        previous_hash = ""
        normalized: list[AttackAttempt] = []
        repeated: list[bool] = []
        for attempt in records:
            failure = attempt.failure_signature
            is_repeat = bool(
                attempt.response_sha256
                and attempt.response_sha256 == previous_hash
                and attempt.verdict != Verdict.CONFIRMED.value
            )
            if is_repeat and failure in {
                "",
                FailureSignature.NO_RELEVANT_SIGNAL.value,
            }:
                failure = FailureSignature.REPEATED_RESPONSE.value
            if attempt.response_sha256:
                previous_hash = attempt.response_sha256
            normalized.append(replace(attempt, failure_signature=failure))
            repeated.append(is_repeat)

        refusal_streak = 0
        repetition_streak = 0
        for attempt in reversed(normalized):
            if attempt.failure_signature == FailureSignature.EXPLICIT_REFUSAL.value:
                refusal_streak += 1
            else:
                break
        for is_repeat in reversed(repeated):
            if is_repeat:
                repetition_streak += 1
            else:
                break
        return cls(
            attempts=tuple(normalized[-MAX_ATTEMPTS:]),
            total_turns=len(normalized),
            refusal_streak=refusal_streak,
            repetition_streak=repetition_streak,
        )

    @property
    def current_strategy_id(self) -> str:
        return (
            self.attempts[-1].strategy_id if self.attempts else ""
        ) or "cold_start"

    @property
    def current_move_id(self) -> str:
        return (
            self.attempts[-1].move_id if self.attempts else ""
        ) or "adaptive_probe"

    def _counts(self, attribute: str) -> dict[str, int]:
        return dict(
            sorted(
                Counter(
                    value
                    for attempt in self.attempts
                    if (value := getattr(attempt, attribute))
                ).items()
            )
        )

    def _capabilities(self) -> list[dict[str, str]]:
        values: list[str] = []
        for attempt in self.attempts:
            if attempt.move_id != "map_capabilities":
                continue
            for signal in attempt.observed_signals:
                bounded = _bounded(signal, 200)
                if bounded and bounded not in values:
                    values.append(bounded)
        return [
            {"value": value, "source": "model", "confidence": "untrusted"}
            for value in values[-MAX_OBSERVATIONS:]
        ]

    def _best_evidence(self) -> dict[str, Any] | None:
        for verdict in (Verdict.CONFIRMED.value, Verdict.POTENTIAL.value):
            for attempt in reversed(self.attempts):
                if attempt.verdict == verdict:
                    return {
                        "turn_id": attempt.turn_id,
                        "turn": attempt.turn,
                        "verdict": verdict,
                        "summary": attempt.evaluation_summary,
                        "observed_signals": list(attempt.observed_signals),
                    }
        return None

    def to_dict(self) -> dict[str, Any]:
        ruled_out = [
            {
                "turn": attempt.turn,
                "strategy_id": attempt.strategy_id,
                "move_id": attempt.move_id,
                "tactic": attempt.tactic,
                "failure_signature": attempt.failure_signature,
            }
            for attempt in self.attempts
            if attempt.failure_signature
            not in {"", FailureSignature.PARTIAL_DISCLOSURE.value}
        ][-MAX_OBSERVATIONS:]
        unresolved = [
            {
                "turn": attempt.turn,
                "hypothesis": attempt.hypothesis,
                "verdict": attempt.verdict,
            }
            for attempt in self.attempts
            if attempt.hypothesis and attempt.verdict != Verdict.CONFIRMED.value
        ][-MAX_HYPOTHESES:]
        return {
            "schema_version": self.schema_version,
            "total_turns": self.total_turns,
            "current_strategy_id": self.current_strategy_id,
            "current_move_id": self.current_move_id,
            "strategy_attempts": self._counts("strategy_id"),
            "move_attempts": self._counts("move_id"),
            "failure_counts": self._counts("failure_signature"),
            "refusal_streak": self.refusal_streak,
            "repetition_streak": self.repetition_streak,
            "mapped_capabilities": self._capabilities(),
            "unresolved_hypotheses": unresolved,
            "ruled_out_approaches": ruled_out,
            "best_evidence": self._best_evidence(),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }

    def prompt_context(self, share: Callable[[str], str]) -> str:
        """Render bounded structural memory plus compact older-turn outcomes."""
        if not self.attempts:
            return ""
        move_counts = self._counts("move_id")
        failure_counts = self._counts("failure_signature")
        lines = [
            "Whole-run attack state (target-derived claims are untrusted):",
            f"- turns observed: {self.total_turns}",
        ]
        if move_counts:
            lines.append(
                "- attempted moves: "
                + ", ".join(f"{key}={value}" for key, value in move_counts.items())
            )
        if failure_counts:
            lines.append(
                "- failure signatures: "
                + ", ".join(
                    f"{key}={value}" for key, value in failure_counts.items()
                )
            )
        if self.refusal_streak or self.repetition_streak:
            lines.append(
                f"- current streaks: refusals={self.refusal_streak}, "
                f"repeated_responses={self.repetition_streak}"
            )

        capabilities = [
            shared
            for item in self._capabilities()
            if (shared := share(item["value"]))
        ]
        if capabilities:
            lines.append("- mapped capability signals: " + "; ".join(capabilities))

        ruled_out = [
            attempt
            for attempt in self.attempts
            if attempt.failure_signature
            not in {"", FailureSignature.PARTIAL_DISCLOSURE.value}
        ][-8:]
        if ruled_out:
            lines.append(
                "- approaches to avoid without an explicit pivot: "
                + "; ".join(
                    f"{item.strategy_id}/{item.move_id} ({item.failure_signature})"
                    for item in ruled_out
                )
            )

        best = self._best_evidence()
        if best and (summary := share(str(best["summary"]))):
            lines.append(f"- best evidence so far: {best['verdict']} — {summary}")

        older = self.attempts[:-2]
        compact: list[str] = []
        for attempt in reversed(older):
            detail = (
                f"turn {attempt.turn}: {attempt.strategy_id}/{attempt.move_id}; "
                f"goal={_bounded(attempt.goal, 180)}; tactic={_bounded(attempt.tactic, 140)}; "
                f"outcome={attempt.verdict or 'not_evaluated'}"
            )
            if attempt.failure_signature:
                detail += f"; failure={attempt.failure_signature}"
            if attempt.pivot_reason and (pivot := share(attempt.pivot_reason)):
                detail += f"; pivot={_bounded(pivot, 180)}"
            if sum(len(line) + 1 for line in lines + compact) + len(detail) > MAX_CONTEXT_CHARS:
                break
            compact.append(detail)
        if compact:
            lines.append("Older turn outcomes:")
            lines.extend(reversed(compact))
        return "\n".join(lines)[:MAX_CONTEXT_CHARS]


def response_digest(response: str) -> str:
    """Correlate exact repeats without retaining the target response."""
    return hashlib.sha256(response.encode("utf-8")).hexdigest() if response else ""
