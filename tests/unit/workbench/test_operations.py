"""Tests for the browser operation allowlist.

The allowlist is a security boundary, so these tests assert its exact contents
rather than sampling it. Adding a member must require editing this file.
"""

from __future__ import annotations

import pytest

from stealth_prompt.workbench.operations import (
    ALLOWED_KEYS,
    LOCATOR_PREFERENCE,
    BrowserOperation,
    Locator,
    LocatorStrategy,
    is_allowed_operation,
    parse_key,
    parse_operation,
)


class TestAllowlist:
    def test_exact_membership(self) -> None:
        assert {op.value for op in BrowserOperation} == {
            "pick_locator",
            "fill",
            "click",
            "press",
            "wait_for",
            "extract",
        }

    @pytest.mark.parametrize(
        "name",
        [
            "evaluate",
            "eval",
            "exec",
            "script",
            "add_init_script",
            "route",
            "expose_function",
            "goto",
            "download",
            "raw_cdp",
            "screenshot",
        ],
    )
    def test_execution_shaped_operations_are_absent(self, name: str) -> None:
        assert is_allowed_operation(name) is False

    def test_send_is_not_an_operation(self) -> None:
        # Submitting to the target is a separate operator gesture and must not
        # be reachable by chaining allowed operations.
        assert is_allowed_operation("send") is False
        assert is_allowed_operation("submit") is False

    def test_parse_rejects_unknown_operations_and_lists_the_allowed_set(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            parse_operation("evaluate")

        message = str(excinfo.value)
        assert "is not allowed" in message
        assert "pick_locator" in message

    @pytest.mark.parametrize("op", list(BrowserOperation))
    def test_every_member_round_trips(self, op: BrowserOperation) -> None:
        assert parse_operation(op.value) is op


class TestKeys:
    @pytest.mark.parametrize("key", ["Enter", "Shift+Enter", "Escape", "Tab"])
    def test_allowed_keys_accepted(self, key: str) -> None:
        assert parse_key(key) == key

    @pytest.mark.parametrize(
        "key", ["Meta+Q", "Control+W", "F12", "Control+Shift+I", "Alt+F4", "a"]
    )
    def test_browser_level_shortcuts_refused(self, key: str) -> None:
        with pytest.raises(ValueError, match="is not allowed"):
            parse_key(key)

    def test_allowlist_is_not_empty_and_covers_submission(self) -> None:
        assert "Enter" in ALLOWED_KEYS


class TestLocator:
    def test_blank_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            Locator(strategy=LocatorStrategy.CSS, value="  ")

    def test_role_locator_requires_an_accessible_name(self) -> None:
        with pytest.raises(ValueError, match="accessible name"):
            Locator(strategy=LocatorStrategy.ROLE, value="button")

    def test_role_locator_with_a_name_is_accepted(self) -> None:
        locator = Locator(strategy=LocatorStrategy.ROLE, value="button", name="Send")

        assert locator.name == "Send"

    def test_accessibility_strategies_are_preferred_over_css(self) -> None:
        role = Locator(strategy=LocatorStrategy.ROLE, value="textbox", name="Message")
        css = Locator(strategy=LocatorStrategy.CSS, value="#msg")

        assert role.preference_rank < css.preference_rank

    def test_css_is_the_last_resort(self) -> None:
        assert LOCATOR_PREFERENCE[-1] is LocatorStrategy.CSS
        assert LOCATOR_PREFERENCE[0] is LocatorStrategy.ROLE

    def test_preference_covers_every_strategy(self) -> None:
        assert set(LOCATOR_PREFERENCE) == set(LocatorStrategy)
