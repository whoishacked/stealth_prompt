"""Compatibility check against a real, installed Codex CLI.

Recorded fixtures prove the adapter parses the shapes the schema *declares*.
This proves the installed binary actually accepts what the adapter sends: it
starts a thread, reads back the thread id and effective model, and closes.

No prompt is submitted, so no model is invoked and nothing is billed. The test
skips itself when Codex is absent, so ordinary CI never depends on it.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess  # noqa: S404 - argv-only, shell=False
import tempfile
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

import pytest

from stealth_prompt.agents.codex import (
    APPROVAL_POLICY,
    SANDBOX_MODE,
    CodexAdapter,
    generate_schema_files,
    thread_start_params,
)

T = TypeVar("T")

#: Where the ChatGPT desktop app ships the binary on macOS, plus PATH.
_CANDIDATES = ("/Applications/ChatGPT.app/Contents/Resources/codex",)


def find_codex() -> str | None:
    override = os.environ.get("STEALTH_PROMPT_CODEX")
    if override and Path(override).is_file():
        return override
    on_path = shutil.which("codex")
    if on_path:
        return on_path
    for candidate in _CANDIDATES:
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


CODEX = find_codex()

pytestmark = pytest.mark.skipif(
    CODEX is None,
    reason="no Codex CLI found (set STEALTH_PROMPT_CODEX to a binary path)",
)


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def generated_schema() -> dict[str, Any]:
    """Schemas from the binary that is actually installed right now."""
    assert CODEX is not None
    with tempfile.TemporaryDirectory() as directory:
        generate_schema_files(Path(directory), executable=CODEX)
        bundle = Path(directory) / "codex_app_server_protocol.v2.schemas.json"
        document = json.loads(bundle.read_text())
    return document.get("$defs") or document.get("definitions") or {}


@pytest.fixture(scope="module")
def committed() -> dict[str, Any]:
    """The subset committed to the repository."""
    path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "codex"
        / "app_server_v2_subset.json"
    )
    return json.loads(path.read_text())["definitions"]


class TestGeneratedSchemaMatchesFixture:
    """The committed fixture must still describe the installed binary."""

    def test_thread_start_fields_agree(
        self, generated_schema: dict[str, Any], committed: dict[str, Any]
    ) -> None:
        live = set(generated_schema["ThreadStartParams"]["properties"])
        fixture = set(committed["ThreadStartParams"]["properties"])

        assert fixture == live, (
            "the committed fixture no longer matches the installed CLI; "
            "regenerate it before trusting the Codex adapter"
        )

    def test_everything_the_adapter_sends_is_still_declared(
        self, generated_schema: dict[str, Any]
    ) -> None:
        declared = set(generated_schema["ThreadStartParams"]["properties"])

        assert set(thread_start_params(model="m")) <= declared

    def test_sandbox_and_approval_values_still_exist(
        self, generated_schema: dict[str, Any]
    ) -> None:
        assert SANDBOX_MODE in generated_schema["SandboxMode"]["enum"]
        assert APPROVAL_POLICY in generated_schema["AskForApproval"]["oneOf"][0]["enum"]

    def test_thread_id_is_still_nested(
        self, generated_schema: dict[str, Any]
    ) -> None:
        assert "thread" in generated_schema["ThreadStartResponse"]["properties"]
        assert "threadId" not in generated_schema["ThreadStartResponse"]["properties"]


class TestRealAdapterHandshake:
    """Start and close a real app-server session without prompting a model."""

    def test_thread_starts_and_reports_an_effective_model(self) -> None:
        assert CODEX is not None
        adapter = CodexAdapter(executable=CODEX, cwd=tempfile.gettempdir())

        async def scenario() -> tuple[str | None, str | None]:
            try:
                await adapter.start()
                return adapter.thread_id, adapter.effective_model
            finally:
                await adapter.close()

        thread_id, model = run(scenario())

        assert thread_id, "the binary did not return a thread id"
        # The server reports the model it actually chose; that is what the dock
        # shows as "effective model".
        assert model, "the binary did not report an effective model"

    def test_close_leaves_no_child_process(self) -> None:
        assert CODEX is not None
        adapter = CodexAdapter(executable=CODEX, cwd=tempfile.gettempdir())

        async def scenario() -> Any:
            await adapter.start()
            process = adapter._process  # noqa: SLF001 - asserting ownership
            await adapter.close()
            return process

        process = run(scenario())

        assert process is not None
        assert process.running is False

    def test_model_list_is_answered_or_degrades_cleanly(self) -> None:
        assert CODEX is not None
        adapter = CodexAdapter(executable=CODEX, cwd=tempfile.gettempdir())

        async def scenario() -> list[dict[str, Any]]:
            try:
                await adapter.start()
                return await adapter.list_models()
            finally:
                await adapter.close()

        models = run(scenario())

        # Either the server lists models, or it declines and we get []. Both are
        # acceptable; a crash or a hang is not.
        assert isinstance(models, list)
        for entry in models:
            assert isinstance(entry["id"], str) and entry["id"]


class TestSchemaGenerationHelper:
    def test_writes_files_and_never_touches_the_repository(self) -> None:
        assert CODEX is not None
        with tempfile.TemporaryDirectory() as directory:
            written = generate_schema_files(Path(directory), executable=CODEX)

            assert written, "no schema files were produced"
            for path in written:
                assert str(path).startswith(directory)

    def test_missing_binary_raises_rather_than_guessing(self) -> None:
        from stealth_prompt.agents.base import AgentUnavailableError

        with tempfile.TemporaryDirectory() as directory:
            with pytest.raises(AgentUnavailableError, match="not on PATH"):
                generate_schema_files(
                    Path(directory), executable="definitely-not-codex-xyz"
                )


def test_binary_reports_a_version() -> None:
    """Sanity: the binary this suite is validating against actually runs."""
    assert CODEX is not None
    completed = subprocess.run(  # noqa: S603 - argv list, shell=False
        [CODEX, "--version"], capture_output=True, text=True, timeout=30, check=False
    )

    assert completed.returncode == 0
    assert "codex" in (completed.stdout + completed.stderr).lower()
