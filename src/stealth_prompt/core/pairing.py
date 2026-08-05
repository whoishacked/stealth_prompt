"""Pairing between the browser extension and the local Core.

The Core listens on loopback, which stops a remote attacker but not a local one:
any process on the machine, and any page that can be tricked into fetching a
local URL, could otherwise drive the Core. So a connection must present a token,
and a token is only obtainable by typing a short code the operator can read from
the terminal where they started the Core.

Design notes worth stating:

* the pairing code is short and human-transcribable, so it is deliberately
  short-lived and attempt-limited rather than long;
* the token that replaces it is full-entropy and is what gets stored;
* tokens are scoped to an extension origin where one is known, so a token
  leaked to another local process is still not usable from a different origin;
* every comparison is constant-time.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

#: Unambiguous alphabet: no O/0, I/1, so a code read aloud or off a screen
#: cannot be mistyped in the usual ways.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_GROUP = 4
CODE_GROUPS = 2

#: A code is a bearer credential in transit. Keep the window short.
CODE_TTL_SECONDS = 15 * 60
MAX_CODE_ATTEMPTS = 5

TOKEN_BYTES = 32

EXTENSION_ORIGIN_PATTERN = re.compile(r"^chrome-extension://[a-p]{32}$")


class PairingError(RuntimeError):
    """Pairing was refused. The message is safe to show an operator."""


def generate_code() -> str:
    """A short code the operator can read and type: ``ABCD-EFGH``."""
    groups = [
        "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_GROUP))
        for _ in range(CODE_GROUPS)
    ]
    return "-".join(groups)


def normalize_code(value: str) -> str:
    """Accept what a human typed: any case, with or without the separator."""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
    if len(cleaned) != CODE_GROUP * CODE_GROUPS:
        raise PairingError("a pairing code is 8 characters, for example ABCD-EFGH")
    return cleaned


@dataclass(frozen=True)
class PairedClient:
    """A credential the Core has issued."""

    token: str = field(repr=False)
    origin: str
    issued_at: float
    label: str = "browser extension"

    def to_public_dict(self) -> dict[str, Any]:
        """Everything except the token."""
        return {
            "origin": self.origin,
            "issued_at": self.issued_at,
            "label": self.label,
        }


@dataclass
class PairingService:
    """Issues and validates extension credentials.

    One live code at a time: a second ``start_pairing`` invalidates the first,
    so a code left on screen from an earlier attempt cannot still be redeemed.
    """

    code_ttl_seconds: float = CODE_TTL_SECONDS
    max_attempts: int = MAX_CODE_ATTEMPTS
    #: Injectable so tests need no sleeping.
    clock: Any = time.monotonic

    _code: str = field(default="", repr=False)
    _code_expires_at: float = 0.0
    _attempts: int = 0
    _clients: dict[str, PairedClient] = field(default_factory=dict, repr=False)

    # ----------------------------------------------------------------- codes

    def start_pairing(self) -> str:
        """Generate and display a fresh code, invalidating any previous one."""
        code = generate_code()
        self._code = normalize_code(code)
        self._code_expires_at = self.clock() + self.code_ttl_seconds
        self._attempts = 0
        return code

    @property
    def pairing_open(self) -> bool:
        return bool(self._code) and self.clock() < self._code_expires_at

    def cancel_pairing(self) -> None:
        self._code = ""
        self._code_expires_at = 0.0
        self._attempts = 0

    # ---------------------------------------------------------------- tokens

    def redeem(self, code: str, *, origin: str = "") -> str:
        """Exchange a valid code for a scoped token.

        Raises:
            PairingError: no code is open, it expired, too many attempts have
                been made, the origin is not an extension origin, or the code
                does not match.
        """
        if not self._code:
            raise PairingError("pairing is not open; restart the Core to pair")
        if self.clock() >= self._code_expires_at:
            self.cancel_pairing()
            raise PairingError("the pairing code expired; restart the Core to pair")
        if self._attempts >= self.max_attempts:
            self.cancel_pairing()
            raise PairingError("too many pairing attempts; restart the Core to pair")

        if origin and not EXTENSION_ORIGIN_PATTERN.match(origin):
            raise PairingError("pairing is only offered to a browser extension")

        self._attempts += 1
        try:
            candidate = normalize_code(code)
        except PairingError:
            raise
        # Constant time: a timing signal on an 8-character alphabet is small but
        # entirely avoidable.
        if not secrets.compare_digest(candidate, self._code):
            remaining = max(0, self.max_attempts - self._attempts)
            raise PairingError(
                f"that pairing code is not correct ({remaining} attempt(s) left)"
            )

        token = secrets.token_urlsafe(TOKEN_BYTES)
        self._clients[token] = PairedClient(
            token=token, origin=origin, issued_at=time.time()
        )
        # One code, one token.
        self.cancel_pairing()
        return token

    def verify(self, token: str, *, origin: str = "") -> PairedClient:
        """Validate a presented token.

        Raises:
            PairingError: the token is unknown, or is being used from an origin
                it was not issued to.
        """
        if not token:
            raise PairingError("no token presented")
        # Compare against every issued token in constant time so a wrong token
        # cannot be distinguished from an unknown one by timing.
        found: PairedClient | None = None
        for issued, client in self._clients.items():
            if secrets.compare_digest(token, issued):
                found = client
        if found is None:
            raise PairingError("not paired with this Core")
        if found.origin and origin and found.origin != origin:
            raise PairingError("this token was issued to a different extension")
        return found

    def revoke(self, token: str) -> bool:
        """Forget one credential. Returns whether it existed."""
        return self._clients.pop(token, None) is not None

    def revoke_all(self) -> int:
        count = len(self._clients)
        self._clients.clear()
        return count

    @property
    def paired_clients(self) -> tuple[dict[str, Any], ...]:
        """Public metadata for every paired client. Never includes a token."""
        return tuple(client.to_public_dict() for client in self._clients.values())
