"""Persisted, versioned target bindings.

A binding records how to talk to one target's chat UI: which element is the
input, how to submit, and where the reply appears. It exists so the operator
picks elements once rather than at the start of every run.

What a binding is *not* is equally important. It never holds cookies, storage
state, credentials, broker tokens, or any target response. It is a description
of page structure, and it is stored outside the browser profile precisely so a
throwaway profile can still reuse a reviewed binding.

Bindings are read from disk and are therefore treated as untrusted input:
unknown keys are rejected, the schema version must match, and paths are checked
for traversal and symlinks before anything is opened.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .artifacts import DIR_MODE, FILE_MODE, _chmod
from .config import target_origin_of
from .operations import LocatorStrategy, SubmitAction

BINDING_SCHEMA_VERSION = 1

DEFAULT_BINDING_ROOT = Path.home() / ".stealth-prompt" / "bindings"

#: Profile names double as path components, so the pattern is the traversal
#: control: a matching name has no separator, no ``..``, and no leading dot.
PROFILE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

#: Capture defaults. 1500 ms is deliberately generous: a streamed reply commonly
#: pauses for a second between chunks, and a shorter quiet period reports a
#: half-written answer as complete.
DEFAULT_STABLE_MS = 1500
DEFAULT_CAPTURE_TIMEOUT_MS = 60_000
MIN_STABLE_MS = 250
MAX_CAPTURE_TIMEOUT_MS = 600_000

_ALLOWED_PICK = {"first", "last"}


class BindingError(ValueError):
    """A binding is missing, malformed, unsafe, or of an unknown version."""


def _require_keys(document: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = set(document) - allowed
    if unknown:
        raise BindingError(f"unknown {where} fields: {sorted(unknown)}")


@dataclass(frozen=True)
class BoundLocator:
    """One element reference, with an optional CSS fallback."""

    strategy: LocatorStrategy
    value: str
    name: str | None = None
    css_fallback: str | None = None
    pick: str = "first"

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise BindingError("locator value cannot be empty")
        if self.strategy is LocatorStrategy.ROLE and not self.name:
            raise BindingError("a role locator needs an accessible name")
        if self.pick not in _ALLOWED_PICK:
            raise BindingError(f"pick must be one of {sorted(_ALLOWED_PICK)}")

    def describe(self) -> str:
        if self.strategy is LocatorStrategy.ROLE:
            return f'role={self.value} "{self.name}"'
        return f"{self.strategy.value}={self.value}"

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "strategy": self.strategy.value,
            "value": self.value,
            "pick": self.pick,
        }
        if self.name is not None:
            document["name"] = self.name
        if self.css_fallback is not None:
            document["css_fallback"] = self.css_fallback
        return document

    @classmethod
    def from_dict(cls, document: object, *, where: str = "locator") -> BoundLocator:
        if not isinstance(document, dict):
            raise BindingError(f"{where} must be an object")
        _require_keys(
            document, {"strategy", "value", "name", "css_fallback", "pick"}, where
        )
        raw = document.get("strategy")
        if not isinstance(raw, str):
            raise BindingError(f"{where}.strategy must be a string")
        try:
            strategy = LocatorStrategy(raw)
        except ValueError:
            allowed = ", ".join(s.value for s in LocatorStrategy)
            raise BindingError(
                f"{where}.strategy {raw!r} is not supported; allowed: {allowed}"
            ) from None
        value = document.get("value")
        if not isinstance(value, str):
            raise BindingError(f"{where}.value must be a string")
        name = document.get("name")
        if name is not None and not isinstance(name, str):
            raise BindingError(f"{where}.name must be a string")
        css = document.get("css_fallback")
        if css is not None and not isinstance(css, str):
            raise BindingError(f"{where}.css_fallback must be a string")
        pick = document.get("pick", "first")
        if not isinstance(pick, str):
            raise BindingError(f"{where}.pick must be a string")
        return cls(
            strategy=strategy, value=value, name=name, css_fallback=css, pick=pick
        )


@dataclass(frozen=True)
class CaptureSettings:
    """How a reply is recognised as finished."""

    stable_ms: int = DEFAULT_STABLE_MS
    timeout_ms: int = DEFAULT_CAPTURE_TIMEOUT_MS
    pick: str = "last"
    stop_indicator_css: str | None = None
    ready_indicator_css: str | None = None

    def __post_init__(self) -> None:
        if not MIN_STABLE_MS <= self.stable_ms <= 60_000:
            raise BindingError(f"stable_ms must be in {MIN_STABLE_MS}..60000")
        if not 1000 <= self.timeout_ms <= MAX_CAPTURE_TIMEOUT_MS:
            raise BindingError(f"timeout_ms must be in 1000..{MAX_CAPTURE_TIMEOUT_MS}")
        if self.pick not in _ALLOWED_PICK:
            raise BindingError(f"pick must be one of {sorted(_ALLOWED_PICK)}")

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "stable_ms": self.stable_ms,
            "timeout_ms": self.timeout_ms,
            "pick": self.pick,
        }
        if self.stop_indicator_css:
            document["stop_indicator_css"] = self.stop_indicator_css
        if self.ready_indicator_css:
            document["ready_indicator_css"] = self.ready_indicator_css
        return document

    @classmethod
    def from_dict(cls, document: object) -> CaptureSettings:
        if document is None:
            return cls()
        if not isinstance(document, dict):
            raise BindingError("response settings must be an object")
        _require_keys(
            document,
            {
                "stable_ms",
                "timeout_ms",
                "pick",
                "stop_indicator_css",
                "ready_indicator_css",
                "locator",
            },
            "response",
        )
        for key in ("stable_ms", "timeout_ms"):
            if key in document and (
                isinstance(document[key], bool) or not isinstance(document[key], int)
            ):
                raise BindingError(f"response.{key} must be an integer")
        return cls(
            stable_ms=int(document.get("stable_ms", DEFAULT_STABLE_MS)),
            timeout_ms=int(document.get("timeout_ms", DEFAULT_CAPTURE_TIMEOUT_MS)),
            pick=str(document.get("pick", "last")),
            stop_indicator_css=document.get("stop_indicator_css"),
            ready_indicator_css=document.get("ready_indicator_css"),
        )


@dataclass(frozen=True)
class TargetBinding:
    """A complete, validated description of one target's chat UI."""

    target_origin: str
    input: BoundLocator
    submit_locator: BoundLocator
    submit_action: SubmitAction
    response_locator: BoundLocator
    capture: CaptureSettings = field(default_factory=CaptureSettings)
    profile: str | None = None
    schema_version: int = BINDING_SCHEMA_VERSION
    created_at: str = ""
    validated_at: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != BINDING_SCHEMA_VERSION:
            raise BindingError(
                f"binding schema version {self.schema_version} is not supported "
                f"(this build reads version {BINDING_SCHEMA_VERSION})"
            )
        if not self.target_origin:
            raise BindingError("target_origin cannot be empty")
        if self.profile is not None:
            validate_profile(self.profile)

    def describe(self) -> str:
        return (
            f"input {self.input.describe()} | "
            f"submit {self.submit_action.strategy.value} {self.submit_locator.describe()} | "
            f"reply {self.response_locator.describe()} "
            f"(pick {self.capture.pick}, stable {self.capture.stable_ms} ms)"
        )

    def fingerprint(self) -> dict[str, Any]:
        """Non-secret identity of this binding, safe for a result file."""
        return {
            "target_origin": self.target_origin,
            "profile": self.profile,
            "input": self.input.describe(),
            "submit": f"{self.submit_action.strategy.value}:{self.submit_locator.describe()}",
            "response": self.response_locator.describe(),
            "stable_ms": self.capture.stable_ms,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "target_origin": self.target_origin,
            "profile": self.profile,
            "created_at": self.created_at,
            "validated_at": self.validated_at,
            "input": self.input.to_dict(),
            "submit": {
                **self.submit_action.to_dict(),
                "locator": self.submit_locator.to_dict(),
            },
            "response": {
                **self.capture.to_dict(),
                "locator": self.response_locator.to_dict(),
            },
        }

    @classmethod
    def from_dict(cls, document: object) -> TargetBinding:
        if not isinstance(document, dict):
            raise BindingError("a binding must be a JSON object")
        _require_keys(
            document,
            {
                "schema_version",
                "target_origin",
                "profile",
                "created_at",
                "validated_at",
                "input",
                "submit",
                "response",
            },
            "binding",
        )
        version = document.get("schema_version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise BindingError("schema_version must be an integer")
        if version != BINDING_SCHEMA_VERSION:
            raise BindingError(
                f"binding schema version {version} is not supported "
                f"(this build reads version {BINDING_SCHEMA_VERSION})"
            )

        origin = document.get("target_origin")
        if not isinstance(origin, str) or not origin:
            raise BindingError("target_origin must be a non-empty string")

        submit = document.get("submit")
        if not isinstance(submit, dict):
            raise BindingError("submit must be an object")
        response = document.get("response")
        if not isinstance(response, dict):
            raise BindingError("response must be an object")

        profile = document.get("profile")
        if profile is not None and not isinstance(profile, str):
            raise BindingError("profile must be a string")

        return cls(
            target_origin=origin,
            input=BoundLocator.from_dict(document.get("input"), where="input"),
            submit_locator=BoundLocator.from_dict(
                submit.get("locator"), where="submit.locator"
            ),
            submit_action=SubmitAction.from_dict(
                {k: v for k, v in submit.items() if k != "locator"}
            ),
            response_locator=BoundLocator.from_dict(
                response.get("locator"), where="response.locator"
            ),
            capture=CaptureSettings.from_dict(response),
            profile=profile,
            schema_version=version,
            created_at=str(document.get("created_at") or ""),
            validated_at=str(document.get("validated_at") or ""),
        )

    def with_validation(self, when: str) -> TargetBinding:
        return replace(self, validated_at=when)


def validate_profile(name: str) -> str:
    if not PROFILE_PATTERN.match(name):
        raise BindingError(
            f"profile name {name!r} is not valid; use 1-64 characters of "
            "lowercase letters, digits, dot, underscore, or hyphen"
        )
    return name


def normalize_origin(target: str) -> str:
    """Normalize a URL or origin to the key form used for storage."""
    if "://" not in target:
        raise BindingError(f"{target!r} is not an absolute http(s) URL")
    return target_origin_of(target)


def binding_key(target: str, profile: str | None = None) -> str:
    """Generate the safe filename stem for a binding.

    The origin is never used directly as a path component -- it can contain
    characters a filesystem treats specially. It is slugified, and a short digest
    keeps two different origins from colliding on the same slug.
    """
    import hashlib

    origin = normalize_origin(target)
    slug = re.sub(r"[^a-z0-9]+", "-", origin.lower()).strip("-")[:48] or "target"
    digest = hashlib.sha256(origin.encode("utf-8")).hexdigest()[:10]
    stem = f"{slug}-{digest}"
    if profile:
        stem = f"{stem}--{validate_profile(profile)}"
    return stem


class BindingStore:
    """Reads and writes bindings under one owner-only root directory."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root).expanduser() if root else DEFAULT_BINDING_ROOT

    @property
    def root(self) -> Path:
        return self._root

    def _ensure_root(self) -> Path:
        if self._root.is_symlink():
            raise BindingError(f"refusing to use symlinked binding root {self._root}")
        self._root.mkdir(parents=True, exist_ok=True)
        _chmod(self._root, DIR_MODE)
        return self._root

    def path_for(self, target: str, profile: str | None = None) -> Path:
        return self._root / f"{binding_key(target, profile)}.json"

    def _checked(self, path: Path) -> Path:
        """Reject symlinks and paths that escape the root."""
        if path.is_symlink():
            raise BindingError(f"refusing to follow symlink {path}")
        resolved = path.resolve()
        root = self._root.resolve()
        if root != resolved.parent:
            raise BindingError(f"binding path {path} is outside the binding root")
        return path

    def save(self, binding: TargetBinding) -> Path:
        """Write ``binding`` atomically with owner-only permissions."""
        import tempfile

        self._ensure_root()
        path = self._checked(self.path_for(binding.target_origin, binding.profile))
        payload = json.dumps(binding.to_dict(), indent=2, ensure_ascii=False)

        handle, temp_name = tempfile.mkstemp(dir=self._root, prefix=".tmp-binding-")
        temp_path = Path(temp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            _chmod(temp_path, FILE_MODE)
            os.replace(temp_path, path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        return path

    def load(self, target: str, profile: str | None = None) -> TargetBinding | None:
        """Load a binding, or ``None`` when none has been saved."""
        path = self.path_for(target, profile)
        if not path.exists():
            return None
        return self.load_path(path)

    def load_path(self, path: Path) -> TargetBinding:
        """Load a binding from an explicit path."""
        path = Path(path).expanduser()
        if path.is_symlink():
            raise BindingError(f"refusing to follow symlink {path}")
        if not path.is_file():
            raise BindingError(f"{path} is not a regular file")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BindingError(f"{path} is not valid JSON: {exc.msg}") from None
        return TargetBinding.from_dict(document)

    def list_bindings(self) -> list[tuple[Path, TargetBinding | None, str]]:
        """Return ``(path, binding, error)`` for every stored binding."""
        if not self._root.is_dir():
            return []
        results: list[tuple[Path, TargetBinding | None, str]] = []
        for path in sorted(self._root.glob("*.json")):
            try:
                results.append((path, self.load_path(path), ""))
            except BindingError as exc:
                results.append((path, None, str(exc)))
        return results

    def delete(self, target: str, profile: str | None = None) -> bool:
        """Remove a stored binding. Returns whether one existed."""
        path = self.path_for(target, profile)
        if not path.exists():
            return False
        self._checked(path)
        path.unlink()
        return True
