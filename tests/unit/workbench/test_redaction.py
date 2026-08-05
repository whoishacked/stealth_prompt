"""Tests for redaction and terminal sanitization."""

from __future__ import annotations

import pytest

from stealth_prompt.workbench.redaction import (
    REDACTED,
    bound,
    redact,
    sanitize_for_terminal,
)


class TestRedact:
    @pytest.mark.parametrize(
        "secret",
        [
            "sk-abcdefghijklmnopqrstuvwxyz012345",
            "sk-ant-abcdefghijklmnopqrstuvwxyz",
            "AKIAIOSFODNN7EXAMPLE",
            "ghp_abcdefghijklmnopqrstuvwxyz0123",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N",
        ],
    )
    def test_credential_shapes_are_removed(self, secret: str) -> None:
        result = redact(f"the value is {secret} ok")

        assert secret not in result
        assert REDACTED in result

    def test_authorization_header_removed(self) -> None:
        assert "hunter2" not in redact("Authorization: Bearer hunter2hunter2hunter2")

    def test_cookie_header_removed(self) -> None:
        assert "abc123session" not in redact("Cookie: session=abc123session")

    def test_proxy_userinfo_removed(self) -> None:
        result = redact("http://user:pass@proxy.example:8080/")

        assert "pass" not in result

    def test_private_key_header_removed(self) -> None:
        assert REDACTED in redact("-----BEGIN RSA PRIVATE KEY-----")

    def test_ordinary_prose_is_untouched(self) -> None:
        text = "The assistant refused to share the password."

        assert redact(text) == text

    def test_extra_patterns_are_applied(self) -> None:
        result = redact("code ACME-1234", extra_patterns=(r"ACME-\d+",))

        assert "ACME-1234" not in result

    def test_a_broken_extra_pattern_does_not_disable_builtins(self) -> None:
        # A bad scenario pattern must not silently switch off credential rules.
        result = redact("sk-abcdefghijklmnopqrstuvwxyz012345", extra_patterns=("[bad",))

        assert REDACTED in result

    def test_empty_input(self) -> None:
        assert redact("") == ""


class TestSanitizeForTerminal:
    def test_ansi_escapes_are_stripped(self) -> None:
        result = sanitize_for_terminal("\x1b[31mred\x1b[0m")

        assert "\x1b" not in result
        assert "red" in result

    def test_carriage_returns_removed(self) -> None:
        # \r lets hostile text overwrite a line and forge a log entry.
        assert "\r" not in sanitize_for_terminal("real line\rfake line")

    def test_bidirectional_overrides_removed(self) -> None:
        assert "‮" not in sanitize_for_terminal("safe‮txet desrever")

    def test_control_characters_removed(self) -> None:
        assert "\x00" not in sanitize_for_terminal("null\x00byte")

    def test_newlines_and_tabs_survive(self) -> None:
        assert sanitize_for_terminal("a\nb\tc") == "a\nb\tc"

    def test_long_text_is_bounded_with_a_notice(self) -> None:
        result = sanitize_for_terminal("x" * 5000, limit=100)

        assert len(result) < 200
        assert "more characters" in result


class TestBound:
    def test_short_text_is_unchanged(self) -> None:
        assert bound("hello", max_bytes=100) == ("hello", False)

    def test_long_text_is_truncated(self) -> None:
        text, truncated = bound("0123456789", max_bytes=4)

        assert text == "0123"
        assert truncated is True

    def test_truncation_keeps_valid_utf8(self) -> None:
        text, truncated = bound("\U0001f600\U0001f600", max_bytes=6)

        assert truncated is True
        text.encode("utf-8").decode("utf-8")
        assert text == "\U0001f600"
