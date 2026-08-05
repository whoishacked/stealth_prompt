"""Why a run can or cannot start, computed in Python and shown in the dock.

A disabled button with no explanation is the worst possible failure mode for a
security tool: the operator cannot tell whether they misconfigured something,
hit a bug, or are looking at an unsupported combination. So readiness is a
*list of named checks*, each carrying an actionable reason, and the dock renders
it next to Start rather than silently greying the button out.

Every check is computed here, from the authoritative configuration, so the dock
never has to reason about internal state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from .config import RunMode, TargetDataSharing, WorkbenchConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .binding import TargetBinding


class CheckState(str, Enum):
    """Outcome of one readiness check."""

    OK = "ok"
    #: Usable, but the operator should know something.
    WARN = "warn"
    #: Blocks the run.
    BLOCKED = "blocked"
    #: Does not apply in this mode.
    NOT_REQUIRED = "not_required"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class ReadinessCheck:
    """One named prerequisite."""

    key: str
    label: str
    state: CheckState
    detail: str = ""
    #: What the operator should do. Empty when nothing is needed.
    action: str = ""

    @property
    def blocking(self) -> bool:
        return self.state is CheckState.BLOCKED

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "state": self.state.value,
            "detail": self.detail,
            "action": self.action,
        }


@dataclass(frozen=True)
class Readiness:
    """The full checklist plus the one-line summary Start shows."""

    checks: tuple[ReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        return not any(check.blocking for check in self.checks)

    @property
    def blockers(self) -> tuple[ReadinessCheck, ...]:
        return tuple(check for check in self.checks if check.blocking)

    def summary(self) -> str:
        """The sentence shown beside Start."""
        blockers = self.blockers
        if not blockers:
            return "Ready to start."
        first = blockers[0]
        more = f" (+{len(blockers) - 1} more)" if len(blockers) > 1 else ""
        return f"Start unavailable: {first.action or first.detail}{more}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "summary": self.summary(),
            "checks": [check.to_dict() for check in self.checks],
            "blockers": [check.to_dict() for check in self.blockers],
        }


def _provider_checks(config: WorkbenchConfig) -> list[ReadinessCheck]:
    from ..agents.registry import (
        PROVIDERS,
        ProviderError,
        health_state,
        parse_provider,
    )

    checks: list[ReadinessCheck] = []
    try:
        kind = parse_provider(config.agent.provider)
    except ProviderError as exc:
        return [
            ReadinessCheck(
                key="provider",
                label="Provider selected",
                state=CheckState.BLOCKED,
                detail=str(exc),
                action="choose a supported backend",
            )
        ]

    spec = PROVIDERS[kind]
    checks.append(
        ReadinessCheck(
            key="provider",
            label="Provider selected",
            state=CheckState.OK,
            detail=spec.label,
        )
    )

    state, detail, remedy = health_state(kind)
    if state == "not_installed":
        checks.append(
            ReadinessCheck(
                key="provider_installed",
                label="Provider installed",
                state=CheckState.BLOCKED,
                detail=detail,
                action=remedy or f"install {spec.label}",
            )
        )
    elif state == "unavailable":
        checks.append(
            ReadinessCheck(
                key="provider_installed",
                label="Provider reachable",
                state=CheckState.BLOCKED,
                detail=detail,
                action=remedy or f"make {spec.label} reachable",
            )
        )
    else:
        checks.append(
            ReadinessCheck(
                key="provider_installed",
                label="Provider installed",
                state=CheckState.OK,
                detail=detail,
            )
        )

    # Authentication is reported honestly: for a CLI there is often no
    # non-billable probe, and claiming "authenticated" because `--version`
    # exited zero would be a guess.
    if state == "authenticated":
        auth = ReadinessCheck(
            key="provider_auth",
            label="Provider authenticated",
            state=CheckState.OK,
            detail=detail,
        )
    elif state == "installed_auth_unknown":
        auth = ReadinessCheck(
            key="provider_auth",
            label="Provider authentication",
            state=CheckState.WARN,
            detail="installed; authentication is verified when the session starts",
        )
    elif state == "configured":
        auth = ReadinessCheck(
            key="provider_auth",
            label="Provider authentication",
            state=CheckState.WARN,
            detail="credentials configured; not yet confirmed by the service",
        )
    elif state == "reachable":
        auth = ReadinessCheck(
            key="provider_auth",
            label="Provider reachable",
            state=CheckState.OK,
            detail=detail,
        )
    elif state == "not_configured":
        auth = ReadinessCheck(
            key="provider_auth",
            label="Provider authentication",
            state=CheckState.BLOCKED,
            detail=detail,
            action=remedy or f"configure credentials for {spec.label}",
        )
    else:
        auth = ReadinessCheck(
            key="provider_auth",
            label="Provider authentication",
            state=CheckState.WARN,
            detail=detail,
        )
    checks.append(auth)
    return checks


def evaluate(
    config: WorkbenchConfig,
    *,
    binding: TargetBinding | None,
    binding_saved: bool = False,
    has_captured_reply: bool = False,
) -> Readiness:
    """Compute the checklist for the current configuration."""
    checks: list[ReadinessCheck] = list(_provider_checks(config))

    # --- model ----------------------------------------------------------
    checks.append(
        ReadinessCheck(
            key="model",
            label="Model selected",
            state=CheckState.OK,
            detail=config.agent.model or "backend default",
        )
    )

    # --- mode -----------------------------------------------------------
    checks.append(
        ReadinessCheck(
            key="mode",
            label="Mode selected",
            state=CheckState.OK,
            detail=config.mode.value,
        )
    )

    # --- sharing vs mode -------------------------------------------------
    sharing = config.safety.target_data_sharing
    adaptive_modes = {RunMode.SUPERVISED, RunMode.AUTO}
    if sharing is TargetDataSharing.NONE and config.mode in adaptive_modes:
        checks.append(
            ReadinessCheck(
                key="sharing",
                label="Sharing policy",
                state=CheckState.WARN,
                detail=(
                    "sharing is 'none', so planning uses a static payload "
                    "sequence rather than adapting to replies"
                ),
                action=(
                    "choose redacted or full for adaptive planning"
                ),
            )
        )
    elif sharing is not TargetDataSharing.NONE and not config.agent.is_real_backend:
        checks.append(
            ReadinessCheck(
                key="sharing",
                label="Sharing policy",
                state=CheckState.BLOCKED,
                detail="the fake backend ignores target replies",
                action="choose a real backend, or set sharing to none",
            )
        )
    else:
        checks.append(
            ReadinessCheck(
                key="sharing",
                label="Sharing policy",
                state=CheckState.OK,
                detail=sharing.value,
            )
        )

    # --- locators --------------------------------------------------------
    # Payload-only never touches the page, so it needs no input or send
    # locator at all -- and needs no reply locator either until the operator
    # actually wants to capture something.
    payload_only = config.mode is RunMode.PAYLOAD_ONLY
    for key, label, present in (
        ("input", "Input locator", binding is not None),
        ("submit", "Send locator", binding is not None),
    ):
        if payload_only:
            checks.append(
                ReadinessCheck(
                    key=f"locator_{key}",
                    label=label,
                    state=CheckState.NOT_REQUIRED,
                    detail="payload-only mode never touches the page",
                )
            )
        elif present:
            checks.append(
                ReadinessCheck(
                    key=f"locator_{key}", label=label, state=CheckState.OK
                )
            )
        else:
            checks.append(
                ReadinessCheck(
                    key=f"locator_{key}",
                    label=label,
                    state=CheckState.BLOCKED,
                    detail="not picked",
                    action=f"pick the target {'input' if key == 'input' else 'send'} element",
                )
            )

    if binding is not None:
        reply_check = ReadinessCheck(
            key="locator_response", label="Reply locator", state=CheckState.OK
        )
    elif payload_only and not has_captured_reply:
        reply_check = ReadinessCheck(
            key="locator_response",
            label="Reply locator",
            state=CheckState.NOT_REQUIRED,
            detail="only needed to capture a reply",
        )
    else:
        reply_check = ReadinessCheck(
            key="locator_response",
            label="Reply locator",
            state=CheckState.BLOCKED,
            detail="not picked",
            action="pick the target reply element",
        )
    checks.append(reply_check)

    # --- binding ---------------------------------------------------------
    if payload_only:
        checks.append(
            ReadinessCheck(
                key="binding",
                label="Target binding",
                state=CheckState.NOT_REQUIRED,
                detail="payload-only needs no saved binding",
            )
        )
    elif binding is None:
        checks.append(
            ReadinessCheck(
                key="binding",
                label="Target binding",
                state=CheckState.BLOCKED,
                detail="no validated binding",
                action="save or validate the target binding",
            )
        )
    else:
        checks.append(
            ReadinessCheck(
                key="binding",
                label="Target binding",
                state=CheckState.OK,
                detail="saved" if binding_saved else "loaded",
            )
        )

    # --- objective -------------------------------------------------------
    objective = (config.safety.objective or "").strip()
    checks.append(
        ReadinessCheck(
            key="objective",
            label="Objective present",
            state=CheckState.OK if objective else CheckState.BLOCKED,
            detail=objective[:80] if objective else "empty",
            action="" if objective else "describe what this run should establish",
        )
    )

    # --- limits ----------------------------------------------------------
    limit_problem = ""
    if config.safety.max_turns < 1:
        limit_problem = "max turns must be at least 1"
    elif (
        config.safety.max_duration_seconds is not None
        and config.safety.max_duration_seconds <= 0
    ):
        limit_problem = "max duration must be positive"
    checks.append(
        ReadinessCheck(
            key="limits",
            label="Safety limits valid",
            state=CheckState.BLOCKED if limit_problem else CheckState.OK,
            detail=limit_problem
            or f"{config.safety.max_turns} turns, "
            f"{config.safety.max_duration_seconds or 'no'} s",
            action=limit_problem,
        )
    )

    # --- unattended send confirmation ------------------------------------
    if config.mode is RunMode.AUTO:
        if config.allow_auto_send:
            state, detail = CheckState.OK, "authorized on the command line"
        else:
            # Not blocking: pressing Start *is* the confirmation.
            state, detail = (
                CheckState.WARN,
                "pressing Start confirms unattended sending",
            )
        checks.append(
            ReadinessCheck(
                key="auto_send",
                label="Auto-send confirmation",
                state=state,
                detail=detail,
            )
        )
    else:
        checks.append(
            ReadinessCheck(
                key="auto_send",
                label="Auto-send confirmation",
                state=CheckState.NOT_REQUIRED,
                detail="not an unattended mode",
            )
        )

    # --- anything the config itself refuses ------------------------------
    for problem in config.preflight_problems():
        checks.append(
            ReadinessCheck(
                key="configuration",
                label="Configuration",
                state=CheckState.BLOCKED,
                detail=problem,
                action=problem,
            )
        )

    return Readiness(checks=tuple(checks))
