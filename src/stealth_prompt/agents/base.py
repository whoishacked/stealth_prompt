"""Typed contract between the workbench and a local coding-agent CLI.

The workbench drives Claude Code CLI or Codex CLI as a *payload author*: the
agent proposes English prompt-injection test messages that an operator reviews
before anything reaches the target.

That intent shapes this module in one way worth stating plainly: nothing an
agent emits can become an executable action. The event union below carries
text, usage, and errors only. There is no tool-call, command, script, or
locator field for a model to populate, so a confused or compromised agent has
no channel through which to drive the browser, the shell, or the host. Browser
work is a separate, operator-initiated allowlist
(``stealth_prompt.workbench.operations``).

Adapters are async because every real implementation multiplexes a child
process's stdout while remaining interruptible.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, Protocol, runtime_checkable

# Conservative ceilings. A scenario may lower these but never raise them past
# the point where a runaway agent could exhaust memory or the artifact store.
MAX_PROMPT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
DEFAULT_TIMEOUT_MS = 120_000


class AgentKind(str, Enum):
    """Agent backends the workbench knows how to name."""

    FAKE = "fake"
    CLAUDE = "claude"
    CODEX = "codex"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class AgentEventKind(str, Enum):
    """The complete set of events an adapter may emit.

    Deliberately small. Adding a member that carries an instruction for the
    browser or the host would break the guarantee described in the module
    docstring, so new members must carry data for the operator to *read*.
    """

    SESSION_STARTED = "session_started"
    TEXT_DELTA = "text_delta"
    MESSAGE_COMPLETED = "message_completed"
    USAGE = "usage"
    INTERRUPTED = "interrupted"
    ERROR = "error"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class AgentUsage:
    """Token and cost accounting reported by the agent, when it reports any."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token counts cannot be negative")
        if self.cost_usd is not None and self.cost_usd < 0:
            raise ValueError("cost cannot be negative")


@dataclass
class UsageLedger:
    """Accumulates agent usage across a whole run and enforces a cost ceiling.

    Cost accounting is only as good as what the backend reports. When no turn
    has reported a cost, :attr:`cost_reported` stays false and
    :meth:`would_exceed` returns false with an explicit "unknown" reason -- the
    ledger never pretends to enforce a limit it has no data for.
    """

    max_cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_reported: bool = False
    per_turn: list[dict[str, object]] = field(default_factory=list)

    def record(self, turn: int, usage: AgentUsage | None) -> None:
        """Add one turn's usage."""
        if usage is None:
            self.per_turn.append({"turn": turn, "usage_reported": False})
            return
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        entry: dict[str, object] = {
            "turn": turn,
            "usage_reported": True,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
        }
        if usage.cost_usd is not None:
            self.cost_usd += usage.cost_usd
            self.cost_reported = True
            entry["cost_usd"] = usage.cost_usd
        self.per_turn.append(entry)

    def would_exceed(self) -> tuple[bool, str]:
        """Return ``(stop, reason)`` for starting another planner turn."""
        if self.max_cost_usd is None:
            return False, "no cost limit configured"
        if not self.cost_reported:
            return False, "backend reported no cost; limit cannot be enforced"
        if self.cost_usd >= self.max_cost_usd:
            return True, (
                f"reported cost {self.cost_usd:.4f} USD reached the "
                f"{self.max_cost_usd:.4f} USD limit"
            )
        return False, "within the cost limit"

    def to_dict(self) -> dict[str, object]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            # Distinguish "zero cost" from "the backend never told us".
            "cost_usd": self.cost_usd if self.cost_reported else None,
            "cost_reported": self.cost_reported,
            "max_cost_usd": self.max_cost_usd,
            "per_turn": list(self.per_turn),
        }


@dataclass(frozen=True)
class AgentErrorInfo:
    """A safe, structured error description.

    ``message`` is shown to the operator and must not contain credentials,
    prompts, or target responses. ``code`` is stable enough to branch on.
    """

    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True)
class AgentEvent:
    """One structured event from an agent turn.

    ``text`` is the only free-form field and is always inert display data.
    """

    kind: AgentEventKind
    text: str = ""
    session_id: str | None = None
    usage: AgentUsage | None = None
    error: AgentErrorInfo | None = None
    truncated: bool = False
    sequence: int = 0

    def __post_init__(self) -> None:
        if self.kind is AgentEventKind.ERROR and self.error is None:
            raise ValueError("an ERROR event must carry error information")
        if self.sequence < 0:
            raise ValueError("sequence cannot be negative")


@dataclass(frozen=True)
class AgentRequest:
    """One payload-authoring turn.

    The workbench builds this; an agent never builds one for itself.
    """

    prompt: str
    turn: int = 1
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    max_output_bytes: int = MAX_OUTPUT_BYTES

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("prompt cannot be empty")
        prompt_bytes = len(self.prompt.encode("utf-8"))
        if prompt_bytes > MAX_PROMPT_BYTES:
            raise ValueError(
                f"prompt is {prompt_bytes} bytes, above the {MAX_PROMPT_BYTES}-byte limit"
            )
        if self.turn < 1:
            raise ValueError("turn numbering starts at 1")
        if self.timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        if not 0 < self.max_output_bytes <= MAX_OUTPUT_BYTES:
            raise ValueError(f"max_output_bytes must be in 1..{MAX_OUTPUT_BYTES}")


@dataclass(frozen=True)
class AgentPreflight:
    """The result of checking whether an agent backend is usable.

    Preflight performs no network access and starts no session.
    """

    adapter_name: str
    available: bool
    version: str | None = None
    detail: str = ""
    remedy: str = ""


@dataclass(frozen=True)
class AgentLimits:
    """Bounds the workbench enforces around an adapter."""

    timeout_ms: int = DEFAULT_TIMEOUT_MS
    max_output_bytes: int = MAX_OUTPUT_BYTES
    max_turns: int = 20
    max_cost_usd: float | None = None

    def __post_init__(self) -> None:
        if self.timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        if not 0 < self.max_output_bytes <= MAX_OUTPUT_BYTES:
            raise ValueError(f"max_output_bytes must be in 1..{MAX_OUTPUT_BYTES}")
        if self.max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        if self.max_cost_usd is not None and self.max_cost_usd < 0:
            raise ValueError("max_cost_usd cannot be negative")


class AgentError(RuntimeError):
    """Base class for agent failures.

    Subclasses carry operator-safe messages. Diagnostic detail that may embed
    target or credential material belongs in the restricted run directory, not
    in the exception string.
    """

    code: ClassVar[str] = "agent_error"

    def as_info(self, *, retryable: bool = False) -> AgentErrorInfo:
        return AgentErrorInfo(code=self.code, message=str(self), retryable=retryable)


class AgentUnavailableError(AgentError):
    """The backend is not installed, not on PATH, or too old."""

    code: ClassVar[str] = "agent_unavailable"


class AgentTimeoutError(AgentError):
    """The agent produced no complete turn within the configured timeout."""

    code: ClassVar[str] = "agent_timeout"


class AgentProtocolError(AgentError):
    """The agent emitted output that does not match its documented schema."""

    code: ClassVar[str] = "agent_protocol"


class AgentStateError(AgentError):
    """An adapter method was called in the wrong lifecycle state."""

    code: ClassVar[str] = "agent_state"


@runtime_checkable
class AgentAdapter(Protocol):
    """The interface every agent backend implements.

    Lifecycle is ``preflight`` -> ``start`` -> ``send``* -> ``close``.
    ``close`` is idempotent and must terminate every process the adapter owns.
    """

    adapter_name: ClassVar[str]

    async def preflight(self) -> AgentPreflight:
        """Report whether this backend is usable. Performs no network access."""
        ...

    async def start(self) -> None:
        """Start the agent session, spawning any child process with argv only."""
        ...

    def send(self, request: AgentRequest) -> AsyncIterator[AgentEvent]:
        """Stream the events for one payload-authoring turn."""
        ...

    async def interrupt(self) -> None:
        """Ask the current turn to stop. Safe to call when no turn is active."""
        ...

    async def close(self) -> None:
        """Shut the session down and release every owned resource."""
        ...


@dataclass
class TurnAccumulator:
    """Assembles streamed deltas while enforcing the output byte ceiling.

    Adapters share this so the truncation rule is identical across backends
    rather than reimplemented per transport.
    """

    max_output_bytes: int = MAX_OUTPUT_BYTES
    _chunks: list[str] = field(default_factory=list)
    _size: int = 0
    truncated: bool = False

    def add(self, delta: str) -> str:
        """Append ``delta``, returning the portion actually accepted."""
        if self.truncated or not delta:
            return ""
        remaining = self.max_output_bytes - self._size
        if remaining <= 0:
            self.truncated = True
            return ""

        encoded = delta.encode("utf-8")
        if len(encoded) <= remaining:
            self._chunks.append(delta)
            self._size += len(encoded)
            return delta

        # Cut on a character boundary so the transcript stays valid UTF-8.
        accepted = encoded[:remaining].decode("utf-8", errors="ignore")
        self.truncated = True
        if accepted:
            self._chunks.append(accepted)
            self._size += len(accepted.encode("utf-8"))
        return accepted

    @property
    def text(self) -> str:
        return "".join(self._chunks)

    @property
    def size_bytes(self) -> int:
        return self._size
