"""The allowlisted provider registry.

The browser dock is an untrusted configuration client: it runs inside a page the
*target* controls. So it may name a provider, but it may never supply one. Every
executable path and every endpoint comes from this module; a value arriving from
the extension is only ever matched against the allowlist below, never executed.

The registry also answers two questions the UI must not conflate:

* **installed** -- is the backend present on this machine?
* **authenticated** -- can it actually run a turn?

A provider can be the first without the second, and telling an operator
"available" when a run will immediately fail on credentials is worse than
telling them nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404 - argv-only, shell=False
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .base import AgentAdapter, AgentKind

#: Additional well-known install locations checked after PATH. Each entry is a
#: fixed absolute path, never anything derived from user or page input.
KNOWN_EXECUTABLE_PATHS: dict[str, tuple[str, ...]] = {
    "claude": (),
    "codex": ("/Applications/ChatGPT.app/Contents/Resources/codex",),
}

#: Endpoints a provider may talk to without an explicit override. Loopback only:
#: pointing an "Ollama" backend at an arbitrary remote host would silently turn a
#: local-only configuration into an external disclosure.
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OPENAI_URL = "https://api.openai.com/v1"

#: Environment variables the backend reads. Their *values* never leave Python.
OPENAI_KEY_VARS = ("STEALTH_PROMPT_OPENAI_API_KEY", "OPENAI_API_KEY")


class ProviderKind(str, Enum):
    """Every backend the workbench can be asked for."""

    FAKE = "fake"
    CLAUDE = "claude"
    CODEX = "codex"
    OLLAMA = "ollama"
    OPENAI = "openai"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Backends that predate the registry and are still named ``--agent``.
_AGENT_KIND_ALIASES: dict[str, ProviderKind] = {
    AgentKind.FAKE.value: ProviderKind.FAKE,
    AgentKind.CLAUDE.value: ProviderKind.CLAUDE,
    AgentKind.CODEX.value: ProviderKind.CODEX,
}


@dataclass(frozen=True)
class ProviderSpec:
    """Static facts about a backend, safe to send to the extension."""

    kind: ProviderKind
    label: str
    #: True when using it sends prompts off this machine.
    external: bool
    #: True when it needs credentials beyond being installed.
    needs_auth: bool
    #: True when the backend can enumerate its own models.
    model_discovery: bool
    #: True when a free-text model name is accepted.
    custom_model: bool
    summary: str
    default_model_label: str = "Default"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "label": self.label,
            "external": self.external,
            "needs_auth": self.needs_auth,
            "model_discovery": self.model_discovery,
            "custom_model": self.custom_model,
            "summary": self.summary,
            "default_model_label": self.default_model_label,
        }


PROVIDERS: dict[ProviderKind, ProviderSpec] = {
    ProviderKind.FAKE: ProviderSpec(
        kind=ProviderKind.FAKE,
        label="Fake (testing)",
        external=False,
        needs_auth=False,
        model_discovery=False,
        custom_model=False,
        summary="Deterministic built-in backend. Contacts nothing.",
    ),
    ProviderKind.CLAUDE: ProviderSpec(
        kind=ProviderKind.CLAUDE,
        label="Claude CLI",
        external=True,
        needs_auth=True,
        model_discovery=False,
        custom_model=True,
        summary="Local Claude Code CLI. Prompts leave this machine.",
    ),
    ProviderKind.CODEX: ProviderSpec(
        kind=ProviderKind.CODEX,
        label="Codex CLI",
        external=True,
        needs_auth=True,
        model_discovery=True,
        custom_model=True,
        summary="Local Codex app-server. Prompts leave this machine.",
    ),
    ProviderKind.OLLAMA: ProviderSpec(
        kind=ProviderKind.OLLAMA,
        label="Ollama (local)",
        external=False,
        needs_auth=False,
        model_discovery=True,
        custom_model=True,
        summary="Local Ollama server on loopback. Prompts stay on this machine.",
    ),
    ProviderKind.OPENAI: ProviderSpec(
        kind=ProviderKind.OPENAI,
        label="OpenAI API",
        external=True,
        needs_auth=True,
        model_discovery=True,
        custom_model=True,
        summary="OpenAI-compatible HTTP API. Prompts leave this machine.",
    ),
}


@dataclass(frozen=True)
class ProviderHealth:
    """Whether a backend can actually be used right now."""

    kind: ProviderKind
    installed: bool
    authenticated: bool
    detail: str = ""
    remedy: str = ""
    version: str = ""
    endpoint: str = ""
    #: One of :data:`HEALTH_STATES`. Richer than the two booleans, which are
    #: kept because existing callers read them.
    state: str = "installed_auth_unknown"

    @property
    def usable(self) -> bool:
        """Usable enough to attempt a run.

        ``installed_auth_unknown`` and ``configured`` count: the only honest way
        to confirm those is to try, and refusing to try would make the backend
        unusable forever.
        """
        return self.state in {
            "authenticated",
            "reachable",
            "configured",
            "installed_auth_unknown",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "installed": self.installed,
            "authenticated": self.authenticated,
            "usable": self.usable,
            "detail": self.detail,
            "remedy": self.remedy,
            "version": self.version,
            # An endpoint is a location, never a credential.
            "endpoint": self.endpoint,
            "state": self.state,
        }


@dataclass(frozen=True)
class ProviderSelection:
    """A validated provider + model choice."""

    kind: ProviderKind
    model: str | None = None
    base_url: str | None = None
    executable: str | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.kind.value,
            "model": self.model,
            "base_url": self.base_url,
        }


class ProviderError(ValueError):
    """A provider or model selection is unknown, unsupported, or unsafe."""


def parse_provider(value: str | ProviderKind) -> ProviderKind:
    """Resolve a provider name, accepting the legacy ``--agent`` spellings."""
    if isinstance(value, ProviderKind):
        return value
    text = str(value).strip().lower()
    if text in _AGENT_KIND_ALIASES:
        return _AGENT_KIND_ALIASES[text]
    try:
        return ProviderKind(text)
    except ValueError:
        known = ", ".join(k.value for k in ProviderKind)
        raise ProviderError(
            f"unknown provider {value!r}; known providers are: {known}"
        ) from None


#: Model names are passed to a CLI as argv or to an API as a JSON field. Keep
#: them boring so neither can be surprised by them.
_MODEL_MAX = 128
_MODEL_FORBIDDEN = set(" \t\r\n\x00;|&$`<>\\'\"")


def validate_model(value: str | None) -> str | None:
    """Validate an operator-supplied model name.

    Returns ``None`` for "use the backend default". Rejects anything with shell
    or whitespace characters even though nothing is passed through a shell --
    a model name has no legitimate reason to contain them.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > _MODEL_MAX:
        raise ProviderError(f"model name is longer than {_MODEL_MAX} characters")
    bad = sorted(_MODEL_FORBIDDEN & set(text))
    if bad:
        raise ProviderError(f"model name contains disallowed characters: {bad}")
    return text


def _is_loopback_url(url: str) -> bool:
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    return host in {"127.0.0.1", "::1", "localhost"}


def validate_base_url(kind: ProviderKind, url: str | None) -> str | None:
    """Validate a provider endpoint against the allowlist for that provider."""
    if url is None:
        return None
    text = url.strip()
    if not text:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        raise ProviderError(f"provider URL must be http(s), got {text!r}")
    if not parsed.hostname:
        raise ProviderError(f"provider URL {text!r} has no host")

    if kind is ProviderKind.OLLAMA and not _is_loopback_url(text):
        # "Local Ollama" that quietly points at a remote host would turn a
        # no-disclosure configuration into an external one.
        raise ProviderError(
            f"Ollama must be loopback; refusing {text!r}. Set "
            "STEALTH_PROMPT_OLLAMA_URL explicitly to override at the "
            "command line, never from the browser."
        )
    return text.rstrip("/")


def resolve_executable(kind: ProviderKind) -> str | None:
    """Find a backend's executable on PATH or in a known install location."""
    name = {ProviderKind.CLAUDE: "claude", ProviderKind.CODEX: "codex"}.get(kind)
    if name is None:
        return None
    found = shutil.which(name)
    if found:
        return found
    for candidate in KNOWN_EXECUTABLE_PATHS.get(name, ()):
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


#: An installed binary does not change version while the workbench is open, and
#: spawning it on every checklist re-render made the dock sluggish.
_version_cache: dict[str, str] = {}


def _probe_version(executable: str, timeout_s: float = 10.0) -> str:
    cached = _version_cache.get(executable)
    if cached is not None:
        return cached
    result = _probe_version_uncached(executable, timeout_s)
    _version_cache[executable] = result
    return result


def _probe_version_uncached(executable: str, timeout_s: float = 10.0) -> str:
    try:
        completed = subprocess.run(  # noqa: S603 - argv list, shell=False
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return (completed.stdout or completed.stderr or "").strip().splitlines()[0][:80]


def openai_api_key() -> str | None:
    """Read the OpenAI key from the environment. Never leaves this process."""
    for name in OPENAI_KEY_VARS:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def ollama_base_url() -> str:
    return os.environ.get("STEALTH_PROMPT_OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")


def openai_base_url() -> str:
    return os.environ.get("STEALTH_PROMPT_OPENAI_URL", DEFAULT_OPENAI_URL).rstrip("/")


#: Health is a spectrum, not a boolean. Calling a CLI "authenticated" because
#: ``--version`` exited zero is a guess, and a wrong one often enough to matter.
HEALTH_STATES = (
    "not_installed",        # the backend is absent
    "installed_auth_unknown",  # present; login cannot be checked without billing
    "not_configured",      # present but missing credentials we can see
    "configured",          # credentials present, service not yet contacted
    "reachable",           # the service answered a non-generation probe
    "authenticated",       # the service accepted our credentials
    "unavailable",         # present but not usable right now
)


#: Health probes spawn a process or open a socket, and the readiness checklist
#: asks for them on every keystroke-driven re-render. A short TTL keeps the UI
#: responsive without ever showing state older than a couple of seconds.
_HEALTH_TTL_S = 3.0
_health_cache: dict[ProviderKind, tuple[float, tuple[str, str, str]]] = {}


def clear_health_cache() -> None:
    """Forget cached probes. Used by tests and after a provider change."""
    _health_cache.clear()
    _version_cache.clear()


def health_state(kind: ProviderKind) -> tuple[str, str, str]:
    """Cached wrapper around :func:`probe_health_state`."""
    import time

    now = time.monotonic()
    cached = _health_cache.get(kind)
    if cached is not None and now - cached[0] < _HEALTH_TTL_S:
        return cached[1]
    result = probe_health_state(kind)
    _health_cache[kind] = (now, result)
    return result


def probe_health_state(kind: ProviderKind) -> tuple[str, str, str]:
    """Return ``(state, detail, remedy)`` for a provider.

    Every probe here is free: a version check, a filesystem lookup, or a list
    endpoint. Nothing that costs money or generates tokens.
    """
    if kind is ProviderKind.FAKE:
        return "authenticated", "built-in deterministic backend", ""

    if kind in {ProviderKind.CLAUDE, ProviderKind.CODEX}:
        executable = resolve_executable(kind)
        if executable is None:
            name = "claude" if kind is ProviderKind.CLAUDE else "codex"
            remedy = (
                "install Claude Code: https://claude.com/claude-code"
                if kind is ProviderKind.CLAUDE
                else "install the Codex CLI, or the ChatGPT desktop app"
            )
            return "not_installed", f"{name!r} was not found on PATH", remedy
        version = _probe_version(executable)
        if not version:
            return (
                "unavailable",
                f"{executable} did not report a version",
                f"run `{executable} --version` to check the install",
            )
        # There is no non-billable way to confirm the CLI is logged in, so say
        # so rather than implying a check happened.
        return (
            "installed_auth_unknown",
            f"{version} at {executable}; "
            "authentication will be verified when the first session starts",
            "",
        )

    if kind is ProviderKind.OLLAMA:
        url = ollama_base_url()
        try:
            validate_base_url(kind, url)
        except ProviderError as exc:
            return (
                "unavailable",
                str(exc),
                "point STEALTH_PROMPT_OLLAMA_URL at a loopback address",
            )
        # A configured URL is not a running server. Ask the tags endpoint,
        # which lists models and generates nothing.
        if _ollama_reachable(url):
            return "reachable", f"server answered at {url}", ""
        return (
            "unavailable",
            f"no Ollama server answered at {url}",
            "start Ollama (`ollama serve`) and pull a model",
        )

    if kind is ProviderKind.OPENAI:
        if openai_api_key() is None:
            return (
                "not_configured",
                "no API key in the environment",
                f"export {OPENAI_KEY_VARS[0]}=... before launching",
            )
        # A key is not a working key. Authentication is only confirmed by a
        # successful call, which happens on the first turn.
        return (
            "configured",
            f"API key present; {openai_base_url()} not yet contacted",
            "",
        )

    return "unavailable", f"no health check for {kind}", ""  # pragma: no cover


def _ollama_reachable(base_url: str, timeout_s: float = 1.5) -> bool:
    """Cheap liveness probe against the tags endpoint."""
    import json as _json
    import urllib.error
    import urllib.request

    try:
        request = urllib.request.Request(f"{base_url}/api/tags", method="GET")  # noqa: S310
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
            _json.loads(response.read().decode("utf-8"))
        return True
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False


def check_health(kind: ProviderKind) -> ProviderHealth:
    """Report installation and authentication separately.

    Performs no model call. For network backends it reports whether credentials
    and an endpoint are *configured*, not whether the service answered -- that
    would be a paid request during what the operator thinks is a settings screen.
    """
    state, detail, remedy = health_state(kind)
    endpoint = ""
    if kind is ProviderKind.OLLAMA:
        endpoint = ollama_base_url()
    elif kind is ProviderKind.OPENAI:
        endpoint = openai_base_url()
    version = ""
    if kind in {ProviderKind.CLAUDE, ProviderKind.CODEX}:
        executable = resolve_executable(kind)
        if executable is not None:
            version = _probe_version(executable)

    installed = state not in {"not_installed"}
    # Only claim authentication when something actually confirmed it.
    authenticated = state in {"authenticated", "reachable"}
    return ProviderHealth(
        kind=kind,
        installed=installed,
        authenticated=authenticated,
        detail=detail,
        remedy=remedy,
        version=version,
        endpoint=endpoint,
        state=state,
    )


def health_report() -> list[dict[str, Any]]:
    """Health for every provider, safe to send to the extension."""
    return [check_health(kind).to_dict() for kind in ProviderKind]


def capability_report() -> list[dict[str, Any]]:
    """Static provider capabilities, safe to send to the extension."""
    return [PROVIDERS[kind].to_dict() for kind in ProviderKind]


def build_adapter(
    selection: ProviderSelection,
    *,
    timeout_ms: int = 120_000,
) -> AgentAdapter:
    """Construct the adapter for a validated selection.

    Every path and endpoint used here comes from the registry or the process
    environment. Nothing supplied by the extension reaches a subprocess or a URL.
    """
    kind = selection.kind
    model = validate_model(selection.model)

    if kind is ProviderKind.FAKE:
        from .fake import FakeAgentAdapter

        return FakeAgentAdapter()

    if kind is ProviderKind.CLAUDE:
        from .claude import ClaudeAdapter

        executable = resolve_executable(kind)
        if executable is None:
            raise ProviderError("the Claude CLI is not installed")
        return ClaudeAdapter(executable=executable, model=model)

    if kind is ProviderKind.CODEX:
        from .codex import CodexAdapter

        executable = resolve_executable(kind)
        if executable is None:
            raise ProviderError("the Codex CLI is not installed")
        return CodexAdapter(executable=executable, model=model)

    if kind is ProviderKind.OLLAMA:
        from .ollama import OllamaAdapter

        url = validate_base_url(kind, selection.base_url or ollama_base_url())
        return OllamaAdapter(base_url=url or DEFAULT_OLLAMA_URL, model=model,
                             timeout_ms=timeout_ms)

    if kind is ProviderKind.OPENAI:
        from .openai_api import OpenAIAdapter

        key = openai_api_key()
        if key is None:
            raise ProviderError(
                f"no OpenAI API key; export {OPENAI_KEY_VARS[0]} before launching"
            )
        url = validate_base_url(kind, selection.base_url or openai_base_url())
        return OpenAIAdapter(
            api_key=key,
            base_url=url or DEFAULT_OPENAI_URL,
            model=model,
            timeout_ms=timeout_ms,
        )

    raise ProviderError(f"no constructor for {kind}")  # pragma: no cover


async def discover_models(selection: ProviderSelection) -> list[dict[str, Any]]:
    """List a backend's models, or return an empty list.

    A model list is a convenience. It must never block configuring a run, so
    every failure degrades to "no list available" and the UI falls back to
    Default plus a custom name.
    """
    kind = selection.kind
    spec = PROVIDERS[kind]
    if not spec.model_discovery:
        return []

    if kind is ProviderKind.CODEX:
        adapter: Any = build_adapter(selection)
        try:
            await adapter.start()
            return await adapter.list_models()
        except Exception:  # noqa: BLE001 - discovery is best effort
            return []
        finally:
            await adapter.close()

    if kind is ProviderKind.OLLAMA:
        from .ollama import list_ollama_models

        url = validate_base_url(kind, selection.base_url or ollama_base_url())
        return await list_ollama_models(url or DEFAULT_OLLAMA_URL)

    if kind is ProviderKind.OPENAI:
        from .openai_api import list_openai_models

        key = openai_api_key()
        if key is None:
            return []
        url = validate_base_url(kind, selection.base_url or openai_base_url())
        return await list_openai_models(url or DEFAULT_OPENAI_URL, key)

    return []


def implemented_providers() -> Sequence[ProviderKind]:
    return tuple(ProviderKind)
