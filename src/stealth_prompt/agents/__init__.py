"""Agent backends for the browser workbench.

The registry is an explicit name-to-constructor mapping rather than branching
inside the workbench. Version 1 does not load adapter code named by an
untrusted file: registration is an install-time decision made here in source.
"""

from __future__ import annotations

from .base import (
    DEFAULT_TIMEOUT_MS,
    MAX_OUTPUT_BYTES,
    MAX_PROMPT_BYTES,
    AgentAdapter,
    AgentError,
    AgentErrorInfo,
    AgentEvent,
    AgentEventKind,
    AgentKind,
    AgentLimits,
    AgentPreflight,
    AgentProtocolError,
    AgentRequest,
    AgentStateError,
    AgentTimeoutError,
    AgentUnavailableError,
    AgentUsage,
    TurnAccumulator,
    UsageLedger,
)
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .fake import FakeAgentAdapter

__all__ = [
    "ClaudeAdapter",
    "CodexAdapter",
    "DEFAULT_TIMEOUT_MS",
    "MAX_OUTPUT_BYTES",
    "MAX_PROMPT_BYTES",
    "AgentAdapter",
    "AgentError",
    "AgentErrorInfo",
    "AgentEvent",
    "AgentEventKind",
    "AgentKind",
    "AgentLimits",
    "AgentPreflight",
    "AgentProtocolError",
    "AgentRequest",
    "AgentStateError",
    "AgentTimeoutError",
    "AgentUnavailableError",
    "AgentUsage",
    "FakeAgentAdapter",
    "TurnAccumulator",
    "UsageLedger",
    "build_agent_adapter",
    "implemented_agent_kinds",
]

def implemented_agent_kinds() -> tuple[AgentKind, ...]:
    """Return the backends that can currently be constructed."""
    return tuple(AgentKind)


def build_agent_adapter(
    kind: AgentKind | str,
    *,
    executable: str | None = None,
    model: str | None = None,
) -> AgentAdapter:
    """Construct an agent adapter by name.

    Raises:
        ValueError: the name is not a known backend.
    """
    try:
        resolved = AgentKind(kind)
    except ValueError:
        known = ", ".join(k.value for k in AgentKind)
        raise ValueError(f"unknown agent {kind!r}; known agents are: {known}") from None

    if resolved is AgentKind.FAKE:
        return FakeAgentAdapter()
    if resolved is AgentKind.CLAUDE:
        return ClaudeAdapter(
            executable=executable or "claude", model=model
        )
    if resolved is AgentKind.CODEX:
        return CodexAdapter(executable=executable or "codex", model=model)

    raise ValueError(  # pragma: no cover - unreachable while AgentKind is closed
        f"no constructor registered for agent {resolved.value!r}"
    )
