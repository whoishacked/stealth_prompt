"""Versioned, replayable scenario files.

A scenario records *how an authorized assessment was set up* so a second
operator -- or the same one next week -- can reproduce it. It deliberately does
not record the assessment's results; evidence stays in the session export, and
the two files are written independently.

Three rules shape the format.

**A scenario is configuration, never authority.** Importing one restores what to
test, not permission to test it. Automatic-send authorization is transient and
is never carried in a file, so a shared scenario cannot arrive pre-authorized to
mutate a page. Replay still needs live host permission and a fresh binding
validation against the current document.

**A scenario carries no secret and no capture.** Provider *kind* and requested
model are configuration; API keys, cookies, headers, tokens and captured target
responses are not, and the parser rejects them rather than dropping them
quietly. Silently discarding a credential-shaped field would teach operators
that putting one there is safe.

**Parsing fails closed.** Unknown fields, an unknown schema version, or an
oversized document are errors. A scenario that is only partly understood must
not be half-applied to a live target.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..oracles import Oracle, OracleType
from .assistant import (
    AssistMode,
    InteractionBinding,
    PotentialFindingAction,
    ResponseSource,
)
from .contracts import Objective

SCENARIO_SCHEMA_VERSION = 2
SUPPORTED_SCENARIO_VERSIONS = frozenset({1, SCENARIO_SCHEMA_VERSION})
SCENARIO_KIND = "stealth_prompt_scenario"

#: A scenario is small by construction. The cap stops a hostile file from
#: becoming a memory or parse-time problem before any field is inspected.
MAX_SCENARIO_BYTES = 64 * 1024
MAX_NAME_CHARS = 120
MAX_DESCRIPTION_CHARS = 2000
MAX_SCORERS = 32

TOP_LEVEL_FIELDS = {
    "schema_version",
    "kind",
    "name",
    "description",
    "objective",
    "custom_objective",
    "provider",
    "requested_model",
    "mode",
    "response_source",
    "potential_finding_action",
    "limits",
    "sharing",
    "target_origin",
    "binding",
    "scorers",
    "initial_conversation",
    "expected",
}

#: Key fragments that must never appear in a scenario. Matching is on the
#: substring so `openai_api_key`, `authHeader` and `sessionCookie` are all
#: caught. This is a refusal, not a filter: see the module docstring.
FORBIDDEN_KEY_FRAGMENTS = (
    "key",
    "secret",
    "token",
    "password",
    "credential",
    "cookie",
    "header",
    "authorization",
    "bearer",
    "session_id",
    "response_text",
    "captured",
    "transcript",
)

#: Exceptions to the fragment scan: legitimate field names that happen to
#: contain a forbidden substring. Matching is exact, so `key` is permitted (it
#: is the binding's submit keystroke) while `api_key` is still refused.
ALLOWED_KEYS = {"json_field", "requested_model", "key"}


class ScenarioError(ValueError):
    """A scenario file could not be parsed or is not safe to apply."""


class ScenarioVersionError(ScenarioError):
    """The file's schema version is not the one this build speaks.

    Separate from a generic parse error so the panel can say "made by a
    different version" instead of "corrupt", which is a different fix.
    """


@dataclass(frozen=True)
class Scenario:
    """One reproducible assessment setup."""

    name: str
    objective: Objective
    provider: str
    target_origin: str
    description: str = ""
    custom_objective: str = ""
    requested_model: str = ""
    mode: AssistMode = AssistMode.ASSIST
    response_source: ResponseSource = ResponseSource.PAGE
    potential_finding_action: PotentialFindingAction = PotentialFindingAction.REVIEW
    max_turns: int = 20
    max_duration_seconds: int = 0
    sharing: str = "none"
    binding: InteractionBinding | None = None
    scorers: tuple[Oracle, ...] = ()
    #: Whether replay may read the existing on-page conversation for context.
    initial_conversation: str = "none"
    expected: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCENARIO_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": SCENARIO_KIND,
            "name": self.name,
            "description": self.description,
            "objective": self.objective.value,
            "custom_objective": self.custom_objective,
            "provider": self.provider,
            "requested_model": self.requested_model,
            "mode": self.mode.value,
            "response_source": self.response_source.value,
            "potential_finding_action": self.potential_finding_action.value,
            "limits": {
                "max_turns": self.max_turns,
                "max_duration_seconds": self.max_duration_seconds,
            },
            "sharing": self.sharing,
            "target_origin": self.target_origin,
            "binding": self.binding.to_dict() if self.binding else None,
            "scorers": [
                {
                    "scorer_id": rule.oracle_id,
                    "type": rule.oracle_type.value,
                    "pattern": rule.pattern,
                    "json_field": rule.json_field,
                    "case_sensitive": rule.case_sensitive,
                    "redact_match": rule.redact_match,
                }
                for rule in self.scorers
            ],
            "initial_conversation": self.initial_conversation,
            "expected": dict(self.expected),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def preview(self, *, current_origin: str = "") -> dict[str, Any]:
        """What to show the operator *before* anything is applied.

        The origin comparison is the important part: a scenario built against a
        staging host must not be replayed against production because the two
        looked similar in a file listing.
        """
        mismatch = bool(
            current_origin and self.target_origin and current_origin != self.target_origin
        )
        warnings: list[str] = []
        if mismatch:
            warnings.append(
                f"This scenario was recorded against {self.target_origin}; "
                f"the current target is {current_origin}. Confirm the new target "
                "is in scope before replaying."
            )
        if not current_origin:
            warnings.append("Select the authorized target tab before replaying.")
        if self.binding is None or not self.binding.complete:
            warnings.append(
                "The scenario has no complete interaction binding; the elements "
                "must be selected again."
            )
        if self.mode is AssistMode.AUTO:
            warnings.append(
                "Bounded-auto is recorded in this scenario. Automatic sending is "
                "not restored by an import and must be authorized again."
            )
        return {
            "name": self.name,
            "description": self.description,
            "objective": self.objective.value,
            "provider": self.provider,
            "requested_model": self.requested_model,
            "mode": self.mode.value,
            "potential_finding_action": self.potential_finding_action.value,
            "sharing": self.sharing,
            "target_origin": self.target_origin,
            "current_origin": current_origin,
            "origin_mismatch": mismatch,
            "binding_summary": self.binding.summary() if self.binding else "not recorded",
            "scorers": [
                {"scorer_id": rule.oracle_id, "type": rule.oracle_type.value}
                for rule in self.scorers
            ],
            "max_turns": self.max_turns,
            "max_duration_seconds": self.max_duration_seconds,
            "expected": dict(self.expected),
            # Stated on every preview so it is never a surprise at replay time.
            "auto_send_authorized": False,
            "requires_revalidation": True,
            "warnings": warnings,
        }


def _reject_forbidden_keys(node: object, path: str = "") -> None:
    """Refuse any credential- or capture-shaped key, at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            name = str(key)
            here = f"{path}.{name}" if path else name
            if name not in ALLOWED_KEYS:
                lowered = name.lower()
                for fragment in FORBIDDEN_KEY_FRAGMENTS:
                    if fragment in lowered:
                        raise ScenarioError(
                            f"scenario field {here!r} looks like a credential or a "
                            "captured response; scenarios carry configuration only"
                        )
            _reject_forbidden_keys(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_forbidden_keys(value, f"{path}[{index}]")


def _string(document: dict[str, Any], key: str, *, limit: int, default: str = "") -> str:
    value = document.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ScenarioError(f"scenario field {key!r} must be a string")
    return value.strip()[:limit]


def _bounded_int(value: object, *, low: int, high: int, fallback: int, name: str) -> int:
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioError(f"scenario field {name!r} must be an integer")
    if not low <= value <= high:
        raise ScenarioError(f"scenario field {name!r} must be between {low} and {high}")
    return value


def _parse_scorers(raw: object) -> tuple[Oracle, ...]:
    """Build the scorer set, rejecting a malformed rule before any run starts."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ScenarioError("'scorers' must be a list")
    if len(raw) > MAX_SCORERS:
        raise ScenarioError(f"a scenario may define at most {MAX_SCORERS} scorers")
    rules: list[Oracle] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ScenarioError(f"scorer {index} must be an object")
        unknown = set(entry) - {
            "scorer_id",
            "type",
            "pattern",
            "json_field",
            "case_sensitive",
            "redact_match",
        }
        if unknown:
            raise ScenarioError(f"scorer {index} has unknown fields: {sorted(unknown)}")
        scorer_id = _string(entry, "scorer_id", limit=80) or f"scorer-{index}"
        if scorer_id in seen:
            raise ScenarioError(f"duplicate scorer id {scorer_id!r}")
        seen.add(scorer_id)
        try:
            scorer_type = OracleType(str(entry.get("type", "")))
        except ValueError:
            raise ScenarioError(
                f"scorer {scorer_id!r} has unsupported type {entry.get('type')!r}"
            ) from None
        try:
            # Oracle validates the regex and the structured field path, so an
            # unusable rule is refused here rather than during an assessment.
            rules.append(
                Oracle(
                    oracle_id=scorer_id,
                    oracle_type=scorer_type,
                    pattern=_string(entry, "pattern", limit=512),
                    json_field=_string(entry, "json_field", limit=200),
                    case_sensitive=entry.get("case_sensitive", True) is not False,
                    redact_match=entry.get("redact_match", True) is not False,
                )
            )
        except ValueError as exc:
            raise ScenarioError(str(exc)) from None
    return tuple(rules)


def parse_scenario(raw: str | bytes | dict[str, Any]) -> Scenario:
    """Parse a scenario file, failing closed."""
    if isinstance(raw, (str, bytes)):
        if len(raw) > MAX_SCENARIO_BYTES:
            raise ScenarioError(
                f"scenario is larger than the {MAX_SCENARIO_BYTES}-byte limit"
            )
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ScenarioError(f"scenario is not valid JSON: {exc.msg}") from None
    else:
        document = raw
    if not isinstance(document, dict):
        raise ScenarioError("a scenario must be a JSON object")

    version = document.get("schema_version")
    if version not in SUPPORTED_SCENARIO_VERSIONS:
        raise ScenarioVersionError(
            f"scenario schema version {version!r} is not supported; "
            f"this build reads versions 1 and {SCENARIO_SCHEMA_VERSION}"
        )
    kind = document.get("kind")
    if kind != SCENARIO_KIND:
        raise ScenarioError(f"not a Stealth Prompt scenario (kind={kind!r})")

    unknown = set(document) - TOP_LEVEL_FIELDS
    if unknown:
        raise ScenarioError(f"scenario has unknown fields: {sorted(unknown)}")
    _reject_forbidden_keys(document)

    name = _string(document, "name", limit=MAX_NAME_CHARS)
    if not name:
        raise ScenarioError("a scenario needs a name")

    try:
        objective = Objective(_string(document, "objective", limit=80, default="custom"))
    except ValueError:
        raise ScenarioError(
            f"unsupported objective {document.get('objective')!r}"
        ) from None
    try:
        mode = AssistMode(_string(document, "mode", limit=40, default="assist"))
    except ValueError:
        raise ScenarioError(f"unsupported mode {document.get('mode')!r}") from None
    try:
        response_source = ResponseSource(
            _string(document, "response_source", limit=40, default="page")
        )
    except ValueError:
        raise ScenarioError(
            f"unsupported response source {document.get('response_source')!r}"
        ) from None
    try:
        potential_finding_action = PotentialFindingAction(
            _string(
                document,
                "potential_finding_action",
                limit=40,
                default=PotentialFindingAction.REVIEW.value,
            )
        )
    except ValueError:
        raise ScenarioError(
            "unsupported potential finding action "
            f"{document.get('potential_finding_action')!r}"
        ) from None

    sharing = _string(document, "sharing", limit=20, default="none")
    if sharing not in {"none", "redacted", "full"}:
        raise ScenarioError(f"unsupported sharing policy {sharing!r}")

    initial = _string(document, "initial_conversation", limit=20, default="none")
    if initial not in {"none", "capture"}:
        raise ScenarioError(f"unsupported initial_conversation {initial!r}")

    limits = document.get("limits") or {}
    if not isinstance(limits, dict):
        raise ScenarioError("'limits' must be an object")
    unknown_limits = set(limits) - {"max_turns", "max_duration_seconds"}
    if unknown_limits:
        raise ScenarioError(f"'limits' has unknown fields: {sorted(unknown_limits)}")

    binding_document = document.get("binding")
    binding: InteractionBinding | None = None
    if binding_document:
        try:
            binding = InteractionBinding.from_dict(binding_document)
        except ValueError as exc:
            raise ScenarioError(f"scenario binding is invalid: {exc}") from None

    expected = document.get("expected") or {}
    if not isinstance(expected, dict):
        raise ScenarioError("'expected' must be an object")
    unknown_expected = set(expected) - {"verdict", "notes"}
    if unknown_expected:
        raise ScenarioError(f"'expected' has unknown fields: {sorted(unknown_expected)}")

    return Scenario(
        name=name,
        description=_string(document, "description", limit=MAX_DESCRIPTION_CHARS),
        objective=objective,
        custom_objective=_string(document, "custom_objective", limit=MAX_DESCRIPTION_CHARS),
        provider=_string(document, "provider", limit=40, default="fake"),
        requested_model=_string(document, "requested_model", limit=120),
        mode=mode,
        response_source=response_source,
        potential_finding_action=potential_finding_action,
        max_turns=_bounded_int(
            limits.get("max_turns"), low=0, high=100, fallback=20, name="max_turns"
        ),
        max_duration_seconds=_bounded_int(
            limits.get("max_duration_seconds"),
            low=0,
            high=1800,
            fallback=0,
            name="max_duration_seconds",
        ),
        sharing=sharing,
        target_origin=_string(document, "target_origin", limit=300),
        binding=binding,
        scorers=_parse_scorers(document.get("scorers")),
        initial_conversation=initial,
        expected={
            "verdict": _string(expected, "verdict", limit=40),
            "notes": _string(expected, "notes", limit=MAX_DESCRIPTION_CHARS),
        },
    )


def scenario_from_session(session: Any, *, name: str, description: str = "") -> Scenario:
    """Build an exportable scenario from a live session.

    Reads only configuration off the session. Turns, responses, evaluations and
    the session id are intentionally not consulted: that is the evidence
    export's job, and keeping the two apart is what lets a scenario be shared
    when the evidence cannot be.
    """
    return Scenario(
        name=name.strip()[:MAX_NAME_CHARS] or "Untitled scenario",
        description=description.strip()[:MAX_DESCRIPTION_CHARS],
        objective=session.objective,
        custom_objective=str(getattr(session, "custom_objective", ""))[
            :MAX_DESCRIPTION_CHARS
        ],
        provider=session.provider,
        requested_model=session.model or "",
        mode=session.mode,
        response_source=session.response_source,
        potential_finding_action=session.potential_finding_action,
        max_turns=session.max_turns,
        max_duration_seconds=session.max_duration_seconds,
        sharing=session.sharing.value,
        target_origin=session.origin,
        binding=session.binding,
        scorers=tuple(session.oracles),
    )
