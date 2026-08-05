"""Tests for the allowlisted provider registry.

The registry is the boundary between an untrusted configuration client and the
things that actually execute: subprocesses, endpoints, credentials. These tests
are mostly about what it refuses.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

import pytest

from stealth_prompt.agents.registry import (
    DEFAULT_OLLAMA_URL,
    OPENAI_KEY_VARS,
    PROVIDERS,
    ProviderError,
    ProviderKind,
    ProviderSelection,
    build_adapter,
    capability_report,
    check_health,
    discover_models,
    health_report,
    openai_api_key,
    parse_provider,
    resolve_executable,
    validate_base_url,
    validate_model,
)

T = TypeVar("T")


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


class TestParseProvider:
    @pytest.mark.parametrize(
        "name", ["fake", "claude", "codex", "ollama", "openai"]
    )
    def test_known_providers(self, name: str) -> None:
        assert parse_provider(name).value == name

    def test_legacy_agent_names_still_resolve(self) -> None:
        # `--agent claude` must keep working.
        assert parse_provider("claude") is ProviderKind.CLAUDE

    def test_unknown_provider_lists_the_known_ones(self) -> None:
        with pytest.raises(ProviderError) as excinfo:
            parse_provider("gpt-9")

        message = str(excinfo.value)
        assert "unknown provider" in message
        assert "ollama" in message

    def test_case_and_whitespace_tolerated(self) -> None:
        assert parse_provider("  Codex  ") is ProviderKind.CODEX


class TestValidateModel:
    def test_none_and_blank_mean_default(self) -> None:
        assert validate_model(None) is None
        assert validate_model("   ") is None

    @pytest.mark.parametrize(
        "name", ["gpt-5.6-sol", "llama3", "claude-sonnet-4-5", "qwen2.5:14b-instruct"]
    )
    def test_ordinary_model_names_pass(self, name: str) -> None:
        assert validate_model(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "model; rm -rf /",
            "model && curl evil",
            "model | tee",
            "model`whoami`",
            "model$(id)",
            "model with space",
            "model\nnewline",
            "model'quote",
            'model"quote',
            "model<redirect",
        ],
    )
    def test_shell_shaped_names_refused(self, name: str) -> None:
        # Nothing is passed through a shell, but a model name has no reason to
        # look like this, and refusing is cheaper than reasoning about it.
        with pytest.raises(ProviderError, match="disallowed characters"):
            validate_model(name)

    def test_absurdly_long_name_refused(self) -> None:
        with pytest.raises(ProviderError, match="longer than"):
            validate_model("x" * 200)


class TestValidateBaseUrl:
    def test_none_means_default(self) -> None:
        assert validate_base_url(ProviderKind.OLLAMA, None) is None

    @pytest.mark.parametrize(
        "url", ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:1"]
    )
    def test_loopback_ollama_accepted(self, url: str) -> None:
        assert validate_base_url(ProviderKind.OLLAMA, url)

    @pytest.mark.parametrize(
        "url", ["http://10.0.0.5:11434", "https://ollama.example.com", "http://8.8.8.8"]
    )
    def test_remote_ollama_refused(self, url: str) -> None:
        # "Local Ollama" pointing somewhere else would silently turn a
        # no-disclosure configuration into an external one.
        with pytest.raises(ProviderError, match="must be loopback"):
            validate_base_url(ProviderKind.OLLAMA, url)

    def test_openai_may_be_remote(self) -> None:
        assert validate_base_url(ProviderKind.OPENAI, "https://api.openai.com/v1")

    @pytest.mark.parametrize("url", ["ftp://host/x", "file:///etc/passwd", "notaurl"])
    def test_non_http_schemes_refused(self, url: str) -> None:
        with pytest.raises(ProviderError, match="must be http"):
            validate_base_url(ProviderKind.OPENAI, url)

    def test_trailing_slash_is_normalized(self) -> None:
        assert validate_base_url(
            ProviderKind.OPENAI, "https://api.example/v1/"
        ) == "https://api.example/v1"


class TestCapabilities:
    def test_every_provider_has_a_spec(self) -> None:
        assert set(PROVIDERS) == set(ProviderKind)

    def test_report_is_json_safe_and_secret_free(self) -> None:
        import json

        rendered = json.dumps(capability_report())

        for forbidden in ("api_key", "token", "password", "Authorization"):
            assert forbidden not in rendered

    def test_external_flag_is_accurate(self) -> None:
        assert PROVIDERS[ProviderKind.FAKE].external is False
        assert PROVIDERS[ProviderKind.OLLAMA].external is False
        assert PROVIDERS[ProviderKind.CLAUDE].external is True
        assert PROVIDERS[ProviderKind.OPENAI].external is True

    def test_discovery_capability_is_declared_per_provider(self) -> None:
        assert PROVIDERS[ProviderKind.CODEX].model_discovery is True
        assert PROVIDERS[ProviderKind.OLLAMA].model_discovery is True
        assert PROVIDERS[ProviderKind.CLAUDE].model_discovery is False


class TestHealth:
    def test_fake_is_always_usable(self) -> None:
        health = check_health(ProviderKind.FAKE)

        assert health.installed and health.authenticated and health.usable

    def test_installed_and_authenticated_are_separate(self) -> None:
        # The UI must be able to say "installed but not authenticated".
        health = check_health(ProviderKind.OPENAI)

        assert health.installed is True
        assert health.authenticated == (openai_api_key() is not None)

    def test_openai_without_a_key_names_the_variable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in OPENAI_KEY_VARS:
            monkeypatch.delenv(name, raising=False)

        health = check_health(ProviderKind.OPENAI)

        assert health.authenticated is False
        assert OPENAI_KEY_VARS[0] in health.remedy

    def test_health_never_contains_the_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(OPENAI_KEY_VARS[0], "sk-secret-value-do-not-leak")

        rendered = repr(check_health(ProviderKind.OPENAI).to_dict())

        assert "sk-secret-value-do-not-leak" not in rendered

    def test_report_covers_every_provider(self) -> None:
        assert {entry["kind"] for entry in health_report()} == {
            kind.value for kind in ProviderKind
        }

    def test_ollama_endpoint_defaults_to_loopback(self) -> None:
        health = check_health(ProviderKind.OLLAMA)

        assert health.endpoint.startswith("http://127.0.0.1")


class TestBuildAdapter:
    def test_fake_is_constructible(self) -> None:
        adapter = build_adapter(ProviderSelection(kind=ProviderKind.FAKE))

        assert adapter.adapter_name == "fake"

    def test_model_is_validated_at_construction(self) -> None:
        with pytest.raises(ProviderError, match="disallowed characters"):
            build_adapter(
                ProviderSelection(kind=ProviderKind.FAKE, model="bad; rm -rf /")
            )

    def test_openai_without_a_key_refuses_rather_than_failing_later(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in OPENAI_KEY_VARS:
            monkeypatch.delenv(name, raising=False)

        with pytest.raises(ProviderError, match="no OpenAI API key"):
            build_adapter(ProviderSelection(kind=ProviderKind.OPENAI))

    def test_ollama_adapter_pins_loopback(self) -> None:
        adapter = build_adapter(
            ProviderSelection(kind=ProviderKind.OLLAMA, base_url=DEFAULT_OLLAMA_URL)
        )

        assert adapter.adapter_name == "ollama"

    def test_ollama_refuses_a_remote_endpoint(self) -> None:
        with pytest.raises(ProviderError, match="loopback"):
            build_adapter(
                ProviderSelection(
                    kind=ProviderKind.OLLAMA, base_url="http://10.0.0.5:11434"
                )
            )

    def test_claude_and_codex_use_a_resolved_executable(self) -> None:
        # The registry resolves the path; a caller cannot supply one.
        for kind in (ProviderKind.CLAUDE, ProviderKind.CODEX):
            resolved = resolve_executable(kind)
            if resolved is None:
                continue
            adapter = build_adapter(ProviderSelection(kind=kind))
            assert adapter._executable == resolved  # noqa: SLF001

    def test_a_selection_cannot_smuggle_an_executable(self) -> None:
        # ProviderSelection has an executable field for internal use, but
        # build_adapter always re-resolves from the registry.
        selection = ProviderSelection(
            kind=ProviderKind.FAKE, executable="/bin/sh"
        )

        adapter = build_adapter(selection)

        assert adapter.adapter_name == "fake"


class TestDiscovery:
    def test_providers_without_discovery_return_nothing(self) -> None:
        models = run(discover_models(ProviderSelection(kind=ProviderKind.CLAUDE)))

        assert models == []

    def test_fake_returns_nothing(self) -> None:
        assert run(discover_models(ProviderSelection(kind=ProviderKind.FAKE))) == []

    def test_ollama_discovery_degrades_when_unreachable(self) -> None:
        # Nothing is listening on this loopback port; the call must return an
        # empty list rather than raising, so the UI stays usable.
        models = run(
            discover_models(
                ProviderSelection(
                    kind=ProviderKind.OLLAMA, base_url="http://127.0.0.1:9"
                )
            )
        )

        assert models == []

    def test_openai_discovery_without_a_key_returns_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in OPENAI_KEY_VARS:
            monkeypatch.delenv(name, raising=False)

        assert run(discover_models(ProviderSelection(kind=ProviderKind.OPENAI))) == []


class TestSelectionSerialization:
    def test_never_serializes_an_executable(self) -> None:
        selection = ProviderSelection(
            kind=ProviderKind.CODEX, model="gpt-5", executable="/secret/path"
        )

        rendered = selection.to_dict()

        assert "executable" not in rendered
        assert "/secret/path" not in repr(rendered)

    def test_repr_omits_the_executable(self) -> None:
        selection = ProviderSelection(
            kind=ProviderKind.CODEX, executable="/secret/path"
        )

        assert "/secret/path" not in repr(selection)
