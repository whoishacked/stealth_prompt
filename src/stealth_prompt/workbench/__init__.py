"""Browser workbench: an operator-driven prompt-injection testing session.

The workbench launches an isolated Playwright Chromium against one authorized
target, shows a small assistant dock, and lets an operator ask a local coding
agent for payload text, review it, insert it, approve sending it, and capture
the response.

Phase 1 provides the typed configuration, the agent contract, and the browser
operation allowlist. The broker, extension, and capture loop arrive in later
phases; see ``docs/migration-plan.md``.
"""

from __future__ import annotations

from .config import (
    DEFAULT_MAX_MESSAGE_BYTES,
    LOOPBACK_HOST,
    AgentSettings,
    BrokerSettings,
    BrowserSettings,
    ProfileMode,
    SafetySettings,
    TargetDataSharing,
    WorkbenchConfig,
    WorkbenchConfigError,
    build_workbench_config,
    is_loopback_url,
    target_origin_of,
    validate_profile_name,
)
from .doctor import (
    CheckStatus,
    DoctorCheck,
    DoctorReport,
    Environment,
    ProbeResult,
    SystemEnvironment,
    parse_version,
    run_doctor,
)
from .operations import (
    ALLOWED_KEYS,
    LOCATOR_PREFERENCE,
    BrowserOperation,
    Locator,
    LocatorStrategy,
    is_allowed_operation,
    parse_key,
    parse_operation,
)

__all__ = [
    "ALLOWED_KEYS",
    "DEFAULT_MAX_MESSAGE_BYTES",
    "LOCATOR_PREFERENCE",
    "LOOPBACK_HOST",
    "AgentSettings",
    "BrokerSettings",
    "BrowserOperation",
    "BrowserSettings",
    "CheckStatus",
    "DoctorCheck",
    "DoctorReport",
    "Environment",
    "Locator",
    "LocatorStrategy",
    "ProbeResult",
    "ProfileMode",
    "SafetySettings",
    "SystemEnvironment",
    "TargetDataSharing",
    "WorkbenchConfig",
    "WorkbenchConfigError",
    "build_workbench_config",
    "is_allowed_operation",
    "is_loopback_url",
    "parse_key",
    "parse_operation",
    "parse_version",
    "run_doctor",
    "target_origin_of",
    "validate_profile_name",
]
