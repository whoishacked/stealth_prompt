"""Centralized redaction applied before text crosses a trust boundary.

Two boundaries matter: text sent to an external agent provider, and text written
to a log or console. Both go through here so the rules are defined once.

Redaction is best-effort pattern matching, not a guarantee. It reduces
accidental disclosure of obvious credential shapes; it cannot recognise an
arbitrary secret. The real control is ``target_data_sharing: none``, which sends
nothing at all.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

#: Credential shapes worth catching by default. Ordered longest-first so a more
#: specific pattern wins before a general one.
BUILTIN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("openai_key", r"sk-[A-Za-z0-9_-]{16,}"),
    ("anthropic_key", r"sk-ant-[A-Za-z0-9_-]{16,}"),
    ("bearer", r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*"),
    ("authorization_header", r"(?i)\bauthorization\s*[:=]\s*\S+"),
    ("cookie_header", r"(?i)\bcookie\s*[:=]\s*\S+"),
    ("aws_key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("github_token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("url_userinfo", r"(?i)\b(https?|socks5?)://[^\s/@]+:[^\s/@]+@"),
)

#: C0 control characters except tab/newline, plus the bidirectional overrides
#: that can make logged text render as something other than what it is.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]|\x1b\[[0-9;]*[A-Za-z]|[‪-‮⁦-⁩]")


def redact(text: str, *, extra_patterns: tuple[str, ...] = ()) -> str:
    """Replace credential-shaped substrings in ``text``."""
    if not text:
        return text
    result = text
    for _name, pattern in BUILTIN_PATTERNS:
        result = re.sub(pattern, REDACTED, result)
    for pattern in extra_patterns:
        try:
            result = re.sub(pattern, REDACTED, result)
        except re.error:
            # A bad scenario pattern must not disable the built-in rules.
            continue
    return result


def sanitize_for_terminal(text: str, *, limit: int = 2000) -> str:
    """Make untrusted text safe to print.

    Target responses and agent output can contain ANSI escapes, carriage
    returns, or bidirectional overrides that forge log lines. Strip them, then
    bound the length.
    """
    if not text:
        return text
    cleaned = _CONTROL.sub("", text).replace("\r", "")
    if len(cleaned) > limit:
        return cleaned[:limit] + f"... [{len(cleaned) - limit} more characters]"
    return cleaned


def bound(text: str, *, max_bytes: int) -> tuple[str, bool]:
    """Truncate ``text`` to ``max_bytes``, returning ``(text, truncated)``."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True
