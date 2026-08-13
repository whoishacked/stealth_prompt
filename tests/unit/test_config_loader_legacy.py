"""Characterization tests for the legacy configuration loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.config_loader import ConfigLoader

pytestmark = pytest.mark.usefixtures("clean_environ")


def write_config(directory: Path, config: dict[str, Any], name: str = "config.yaml") -> Path:
    path = directory / name
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


MINIMAL: dict[str, Any] = {
    "llm": {"provider": "ollama"},
    "web": {"url": "http://127.0.0.1:8765/", "method": "GET"},
    "testing": {"max_turns": 3},
}


def test_missing_file_raises_file_not_found(workdir: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Configuration file not found"):
        ConfigLoader(str(workdir / "absent.yaml"))


def test_loads_valid_config(workdir: Path) -> None:
    path = write_config(workdir, MINIMAL)

    loader = ConfigLoader(str(path))

    assert loader.config["llm"]["provider"] == "ollama"
    assert loader.config["web"]["url"] == "http://127.0.0.1:8765/"


class TestEnvironmentSubstitution:
    def test_substitutes_set_variable(
        self, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SP_TEST_MODEL", "resolved-model")
        config = {**MINIMAL, "llm": {"provider": "ollama", "model": "${SP_TEST_MODEL}"}}
        path = write_config(workdir, config)

        loader = ConfigLoader(str(path))

        assert loader.config["llm"]["model"] == "resolved-model"

    def test_uses_default_when_variable_unset(
        self, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SP_TEST_MISSING", raising=False)
        config = {**MINIMAL, "llm": {"provider": "ollama", "model": "${SP_TEST_MISSING:-fallback}"}}
        path = write_config(workdir, config)

        loader = ConfigLoader(str(path))

        assert loader.config["llm"]["model"] == "fallback"

    def test_leaves_placeholder_when_unset_and_no_default(
        self, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Documented behavior: the raw ${VAR} survives so that later validation
        # can report the unresolved reference.
        monkeypatch.delenv("SP_TEST_MISSING", raising=False)
        config = {**MINIMAL, "llm": {"provider": "ollama", "model": "${SP_TEST_MISSING}"}}
        path = write_config(workdir, config)

        loader = ConfigLoader(str(path))

        assert loader.config["llm"]["model"] == "${SP_TEST_MISSING}"

    def test_substitutes_recursively_in_lists_and_nested_dicts(
        self, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SP_TEST_VALUE", "deep")
        config = {
            **MINIMAL,
            "testing": {
                "max_turns": 3,
                "test_types": ["${SP_TEST_VALUE}", "static"],
                "nested": {"inner": {"leaf": "prefix-${SP_TEST_VALUE}-suffix"}},
            },
        }
        path = write_config(workdir, config)

        loader = ConfigLoader(str(path))

        assert loader.config["testing"]["test_types"] == ["deep", "static"]
        assert loader.config["testing"]["nested"]["inner"]["leaf"] == "prefix-deep-suffix"

    def test_non_string_values_are_untouched(
        self, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SP_TEST_VALUE", "x")
        config = {**MINIMAL, "testing": {"max_turns": 7, "flag": True, "ratio": 1.5, "none": None}}
        path = write_config(workdir, config)

        loader = ConfigLoader(str(path))

        testing = loader.config["testing"]
        assert testing["max_turns"] == 7
        assert testing["flag"] is True
        assert testing["ratio"] == 1.5
        assert testing["none"] is None

    def test_multiple_variables_in_one_string(
        self, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SP_TEST_HOST", "127.0.0.1")
        monkeypatch.setenv("SP_TEST_PORT", "8765")
        config = {
            **MINIMAL,
            "web": {"url": "http://${SP_TEST_HOST}:${SP_TEST_PORT}/", "method": "GET"},
        }
        path = write_config(workdir, config)

        loader = ConfigLoader(str(path))

        assert loader.config["web"]["url"] == "http://127.0.0.1:8765/"


class TestValidation:
    @pytest.mark.parametrize("section", ["llm", "web", "testing"])
    def test_missing_required_section_rejected(self, workdir: Path, section: str) -> None:
        config = {k: v for k, v in MINIMAL.items() if k != section}
        path = write_config(workdir, config)

        with pytest.raises(ValueError, match=f"Missing required configuration section: {section}"):
            ConfigLoader(str(path))

    def test_invalid_provider_rejected(self, workdir: Path) -> None:
        config = {**MINIMAL, "llm": {"provider": "anthropic"}}
        path = write_config(workdir, config)

        with pytest.raises(ValueError, match="Invalid LLM provider"):
            ConfigLoader(str(path))

    def test_invalid_web_method_rejected(self, workdir: Path) -> None:
        config = {**MINIMAL, "web": {"url": "http://127.0.0.1:8765/", "method": "DELETE"}}
        path = write_config(workdir, config)

        with pytest.raises(ValueError, match="Invalid HTTP method"):
            ConfigLoader(str(path))

    def test_enabled_proxy_without_url_rejected(self, workdir: Path) -> None:
        config = {**MINIMAL, "proxy": {"enabled": True, "url": ""}}
        path = write_config(workdir, config)

        with pytest.raises(ValueError, match="Proxy is enabled but proxy URL is not provided"):
            ConfigLoader(str(path))

    def test_unsupported_proxy_scheme_rejected(self, workdir: Path) -> None:
        config = {**MINIMAL, "proxy": {"enabled": True, "url": "ftp://127.0.0.1:8080"}}
        path = write_config(workdir, config)

        with pytest.raises(ValueError, match="Invalid proxy URL"):
            ConfigLoader(str(path))

    def test_invalid_proxy_scope_rejected(self, workdir: Path) -> None:
        config = {
            **MINIMAL,
            "proxy": {"enabled": True, "url": "http://127.0.0.1:8080", "scope": "everything"},
        }
        path = write_config(workdir, config)

        with pytest.raises(ValueError, match="Invalid proxy scope"):
            ConfigLoader(str(path))

    def test_valid_proxy_accepted(self, workdir: Path) -> None:
        config = {
            **MINIMAL,
            "proxy": {"enabled": True, "url": "http://127.0.0.1:8080", "scope": "api"},
        }
        path = write_config(workdir, config)

        loader = ConfigLoader(str(path))

        assert loader.config["proxy"]["scope"] == "api"

    def test_disabled_proxy_is_not_validated(self, workdir: Path) -> None:
        config = {**MINIMAL, "proxy": {"enabled": False, "url": "", "scope": "nonsense"}}
        path = write_config(workdir, config)

        loader = ConfigLoader(str(path))

        assert loader.config["proxy"]["enabled"] is False

    def test_empty_yaml_document_fails_without_a_clear_message(self, workdir: Path) -> None:
        # The legacy loader returns a TypeError for an empty document.
        path = workdir / "config.yaml"
        path.write_text("", encoding="utf-8")

        with pytest.raises(TypeError):
            ConfigLoader(str(path))


class TestDotEnvLoading:
    def test_loads_variable_absent_from_environment(
        self, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SP_TEST_FROM_DOTENV", raising=False)
        (workdir / ".env").write_text("SP_TEST_FROM_DOTENV=synthetic-value\n", encoding="utf-8")
        config = {**MINIMAL, "llm": {"provider": "ollama", "model": "${SP_TEST_FROM_DOTENV}"}}
        path = write_config(workdir, config)

        loader = ConfigLoader(str(path))

        assert loader.config["llm"]["model"] == "synthetic-value"

    def test_dotenv_is_read_from_the_working_directory_not_the_config_directory(
        self, workdir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Documented limitation: .env resolution follows the process working
        # directory, so a .env beside the config file is ignored.
        monkeypatch.delenv("SP_TEST_ELSEWHERE", raising=False)
        config_dir = workdir / "elsewhere"
        config_dir.mkdir()
        (config_dir / ".env").write_text("SP_TEST_ELSEWHERE=ignored\n", encoding="utf-8")
        config = {**MINIMAL, "llm": {"provider": "ollama", "model": "${SP_TEST_ELSEWHERE}"}}
        path = write_config(config_dir, config)

        loader = ConfigLoader(str(path))

        assert loader.config["llm"]["model"] == "${SP_TEST_ELSEWHERE}"


class TestGet:
    def test_dot_notation_lookup(self, workdir: Path) -> None:
        path = write_config(workdir, MINIMAL)
        loader = ConfigLoader(str(path))

        assert loader.get("web.url") == "http://127.0.0.1:8765/"

    def test_missing_key_returns_default(self, workdir: Path) -> None:
        path = write_config(workdir, MINIMAL)
        loader = ConfigLoader(str(path))

        assert loader.get("web.absent", "fallback") == "fallback"
        assert loader.get("absent.deeper.key") is None

    def test_traversing_through_a_scalar_returns_default(self, workdir: Path) -> None:
        path = write_config(workdir, MINIMAL)
        loader = ConfigLoader(str(path))

        assert loader.get("web.url.deeper", "fallback") == "fallback"


def test_reload_picks_up_file_changes(workdir: Path) -> None:
    path = write_config(workdir, MINIMAL)
    loader = ConfigLoader(str(path))
    assert loader.config["testing"]["max_turns"] == 3

    updated = {**MINIMAL, "testing": {"max_turns": 9}}
    write_config(workdir, updated)
    loader.reload()

    assert loader.config["testing"]["max_turns"] == 9
