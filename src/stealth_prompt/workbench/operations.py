"""The allowlist of browser operations the workbench will perform.

Every operation the extension can be asked to run is named here. The list is
closed: there is no ``evaluate``, ``script``, ``navigate_arbitrary``, or
``raw_cdp`` member, and adding one would hand a model or a compromised page a
general-purpose execution channel.

Two rules keep this meaningful:

* operations are requested by the *operator*, never by an agent -- the agent
  event union has no field that can name one;
* ``SEND`` is not a member. Submitting a payload to the target is a distinct
  operator gesture, not something reachable by chaining allowed operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Keys accepted by ``press``. Chat UIs need submission and edit keys; anything
# that could trigger a browser-level shortcut is excluded.
ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "Enter",
        "Shift+Enter",
        "Tab",
        "Escape",
        "Backspace",
        "Delete",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "Home",
        "End",
    }
)


class BrowserOperation(str, Enum):
    """Operations the extension may perform on the target page."""

    PICK_LOCATOR = "pick_locator"
    FILL = "fill"
    CLICK = "click"
    PRESS = "press"
    WAIT_FOR = "wait_for"
    EXTRACT = "extract"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class LocatorStrategy(str, Enum):
    """Locator strategies, ordered by preference.

    Accessibility-first: a role/name locator survives a CSS refactor and
    describes what the operator actually meant. A raw CSS selector is the last
    resort, not the default.
    """

    ROLE = "role"
    LABEL = "label"
    PLACEHOLDER = "placeholder"
    TEST_ID = "test_id"
    CSS = "css"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Preference order used when several strategies can address one element.
LOCATOR_PREFERENCE: tuple[LocatorStrategy, ...] = (
    LocatorStrategy.ROLE,
    LocatorStrategy.LABEL,
    LocatorStrategy.PLACEHOLDER,
    LocatorStrategy.TEST_ID,
    LocatorStrategy.CSS,
)


@dataclass(frozen=True)
class Locator:
    """An element reference produced by picking, not by a model."""

    strategy: LocatorStrategy
    value: str
    name: str | None = None

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("locator value cannot be empty")
        if self.strategy is LocatorStrategy.ROLE and not self.name:
            raise ValueError("a role locator needs an accessible name")

    @property
    def preference_rank(self) -> int:
        """Lower is better. Used to choose among candidate locators."""
        return LOCATOR_PREFERENCE.index(self.strategy)


class SubmitStrategy(str, Enum):
    """How a payload is actually submitted to the target.

    Pressing Enter on a *button* -- which is what the first implementation did --
    submits nothing on an ordinary React or Vue chat box: the handler is bound to
    the button's click, or to a keydown on the input. Making the strategy
    explicit is the difference between working on real applications and only on
    plain HTML forms.
    """

    #: Click a selected button element.
    CLICK_BUTTON = "click_button"
    #: Press a key while the chat input is focused.
    PRESS_KEY = "press_key"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class SubmitAction:
    """A validated description of how to submit."""

    strategy: SubmitStrategy = SubmitStrategy.CLICK_BUTTON
    key: str = "Enter"

    def __post_init__(self) -> None:
        if self.strategy is SubmitStrategy.PRESS_KEY:
            parse_key(self.key)

    @property
    def operation(self) -> BrowserOperation:
        return (
            BrowserOperation.CLICK
            if self.strategy is SubmitStrategy.CLICK_BUTTON
            else BrowserOperation.PRESS
        )

    def to_dict(self) -> dict[str, str]:
        return {"strategy": self.strategy.value, "key": self.key}

    @classmethod
    def from_dict(cls, document: object) -> SubmitAction:
        if not isinstance(document, dict):
            raise ValueError("submit action must be an object")
        unknown = set(document) - {"strategy", "key", "action", "locator"}
        if unknown:
            raise ValueError(f"unknown submit fields: {sorted(unknown)}")
        raw = document.get("strategy") or document.get("action") or "click_button"
        # Accept the short spellings a binding file may use.
        alias = {"click": "click_button", "press": "press_key"}
        raw = alias.get(str(raw), str(raw))
        try:
            strategy = SubmitStrategy(raw)
        except ValueError:
            allowed = ", ".join(s.value for s in SubmitStrategy)
            raise ValueError(
                f"submit strategy {raw!r} is not allowed; allowed: {allowed}"
            ) from None
        key = document.get("key", "Enter")
        if not isinstance(key, str):
            raise ValueError("submit key must be a string")
        return cls(strategy=strategy, key=key)


def is_allowed_operation(name: str) -> bool:
    """Return whether ``name`` is an allowed browser operation."""
    return name in {op.value for op in BrowserOperation}


def parse_operation(name: str) -> BrowserOperation:
    """Resolve ``name`` to an operation, refusing anything off the allowlist."""
    try:
        return BrowserOperation(name)
    except ValueError:
        allowed = ", ".join(op.value for op in BrowserOperation)
        raise ValueError(
            f"browser operation {name!r} is not allowed; allowed operations are: {allowed}"
        ) from None


def parse_key(key: str) -> str:
    """Resolve ``key`` for a ``press`` operation against the key allowlist."""
    if key not in ALLOWED_KEYS:
        allowed = ", ".join(sorted(ALLOWED_KEYS))
        raise ValueError(f"key {key!r} is not allowed; allowed keys are: {allowed}")
    return key
