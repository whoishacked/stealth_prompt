"""Shared fixtures and offline fakes for the characterization tests.

Every fake here is deterministic and in-memory. No test in this suite starts a
browser, opens a socket, or contacts a model provider.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable, Iterator
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import pytest


class NetworkAccessAttempted(RuntimeError):
    """Raised when a test tries to reach a host outside this machine."""


_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", ""}


def _is_loopback(address: Any) -> bool:
    """Return whether ``address`` refers to this machine.

    Unix-domain sockets and abstract addresses are local by definition.
    """
    if isinstance(address, (bytes, str)):
        return True
    if not isinstance(address, tuple) or not address:
        return False
    host = address[0]
    if not isinstance(host, str):
        return False
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that tries to reach a host beyond this machine.

    Loopback is allowed because the workbench legitimately runs a broker and a
    demo target on 127.0.0.1, and those deserve real integration coverage. What
    must never happen is a test contacting an authorized target, a model
    provider, or any paid API -- so every non-loopback destination raises.
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection

    def guard(address: Any) -> None:
        if not _is_loopback(address):
            raise NetworkAccessAttempted(
                f"the offline test suite attempted to reach {address!r}; "
                "only loopback is permitted"
            )

    def connect(self: socket.socket, address: Any) -> Any:
        guard(address)
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        guard(address)
        return real_connect_ex(self, address)

    def create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        guard(address)
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run a test in an isolated working directory.

    The legacy code reads ``.env`` and writes ``results/`` relative to the
    process working directory, so tests must not run in the repository root.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def clean_environ() -> Iterator[None]:
    """Restore ``os.environ`` after a test that loads a ``.env`` file."""
    snapshot = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(snapshot)


@pytest.fixture
def base_config(tmp_path: Path) -> dict[str, Any]:
    """A minimal valid legacy configuration pointing only at loopback."""
    return {
        "llm": {
            "provider": "ollama",
            "ollama": {
                "base_url": "http://127.0.0.1:11434",
                "model": "test-model",
                "timeout": 5,
            },
        },
        "web": {
            "url": "http://127.0.0.1:8765/",
            "method": "GET",
            "selenium": {
                "headless": True,
                "selectors": {
                    "input": {"strategy": "id", "value": "message"},
                    "submit": {"strategy": "css", "value": "button[type='submit']"},
                    "response": {"strategy": "class", "value": "answer"},
                },
            },
        },
        "testing": {
            "conversational_mode": True,
            "max_turns": 3,
            "test_types": ["system_prompt_leakage"],
            "tests_per_type": 1,
            "prompt_db_path": str(tmp_path / "successful_prompts.json"),
        },
        "output": {
            "results_dir": "results",
            "format": "json",
        },
    }


class FakeLLMClient:
    """Deterministic stand-in for :class:`src.llm_client.LLMClient`."""

    def __init__(
        self,
        payloads: list[str] | None = None,
        check_results: list[dict[str, Any]] | None = None,
    ) -> None:
        self._payloads = list(payloads or [])
        self._check_results = list(check_results or [])
        self.payload_calls: list[dict[str, Any]] = []
        self.check_calls: list[str] = []

    def generate_payload(
        self,
        test_type: str,
        conversation_history: list[dict[str, str]] | None = None,
        log: bool = True,
    ) -> str:
        self.payload_calls.append(
            {"test_type": test_type, "history_len": len(conversation_history or [])}
        )
        if self._payloads:
            return self._payloads.pop(0)
        return f"generated-payload-{len(self.payload_calls)}"

    def check_sensitive_data(self, response: str, log: bool = True) -> dict[str, Any]:
        self.check_calls.append(response)
        if self._check_results:
            return self._check_results.pop(0)
        return {"found": False, "explanation": "no disclosure", "full_analysis": ""}


class FakeWebAutomation:
    """Deterministic stand-in for :class:`src.web_automation.WebAutomation`."""

    def __init__(
        self,
        responses: list[str | None] | None = None,
        send_results: list[bool] | None = None,
    ) -> None:
        self._responses = list(responses) if responses is not None else []
        self._send_results = list(send_results) if send_results is not None else []
        self.started = 0
        self.closed = 0
        self.sent: list[str] = []

    def start(self) -> None:
        self.started += 1

    def send_prompt(self, prompt: str, log: bool = True) -> bool:
        self.sent.append(prompt)
        if self._send_results:
            return self._send_results.pop(0)
        return True

    def get_response(self, timeout: int | None = None, log: bool = True) -> str | None:
        if self._responses:
            return self._responses.pop(0)
        return f"assistant reply {len(self.sent)}"

    def close(self) -> None:
        self.closed += 1


class FakePromptDB:
    """In-memory stand-in for :class:`src.prompt_db.PromptDB`."""

    def __init__(
        self,
        entries: list[dict[str, Any]] | None = None,
        next_chain_prompt: str | None = None,
        response_matches: bool = False,
    ) -> None:
        self.entries = list(entries or [])
        self.next_chain_prompt = next_chain_prompt
        self.response_matches = response_matches
        self.added: list[dict[str, Any]] = []

    def get_all_prompts(self, test_type: str | None = None) -> list[dict[str, Any]]:
        return self.get_successful_prompts(test_type)

    def get_successful_chains(self, test_type: str | None = None) -> list[dict[str, Any]]:
        return self.get_successful_prompts(test_type)

    def get_successful_prompts(self, test_type: str | None = None) -> list[dict[str, Any]]:
        if test_type is None:
            return list(self.entries)
        return [e for e in self.entries if e.get("test_type") == test_type]

    def try_saved_chain(
        self, test_type: str, current_conversation: list[dict[str, str]]
    ) -> str | None:
        return self.next_chain_prompt

    def check_response_with_prompts(self, response: str, test_type: str) -> bool:
        return self.response_matches

    def add_prompt(
        self,
        prompt: str,
        test_type: str,
        response: str,
        confirmed_by_user: bool = True,
        conversation_chain: list[dict[str, str]] | None = None,
    ) -> None:
        self.added.append(
            {
                "prompt": prompt,
                "test_type": test_type,
                "response": response,
                "confirmed_by_user": confirmed_by_user,
                "conversation_chain": conversation_chain,
            }
        )


def scripted_input(answers: list[str]) -> Callable[[str], str]:
    """Build an ``input``-compatible callable that replays ``answers``."""
    queue = list(answers)

    def _input(prompt: str = "") -> str:
        if not queue:
            raise AssertionError(f"unexpected prompt for operator input: {prompt!r}")
        return queue.pop(0)

    return _input


def no_sleep(seconds: float) -> None:
    """Sleep replacement that keeps the characterization tests fast."""
    return None
