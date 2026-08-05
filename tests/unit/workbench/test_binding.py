"""Tests for persisted target bindings.

A binding is read from disk and drives what the browser does, so it is treated
as untrusted input: unknown keys, wrong versions, and unsafe paths are all
rejected rather than tolerated.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from stealth_prompt.workbench.binding import (
    BINDING_SCHEMA_VERSION,
    DEFAULT_STABLE_MS,
    BindingError,
    BindingStore,
    BoundLocator,
    CaptureSettings,
    TargetBinding,
    binding_key,
    normalize_origin,
    validate_profile,
)
from stealth_prompt.workbench.operations import (
    LocatorStrategy,
    SubmitAction,
    SubmitStrategy,
)

POSIX_ONLY = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX modes")

ORIGIN = "http://127.0.0.1:8765"


def make_binding(**overrides: object) -> TargetBinding:
    defaults: dict[str, object] = {
        "target_origin": ORIGIN,
        "input": BoundLocator(
            strategy=LocatorStrategy.ROLE,
            value="textbox",
            name="Message",
            css_fallback="#message",
        ),
        "submit_locator": BoundLocator(
            strategy=LocatorStrategy.ROLE,
            value="button",
            name="Send",
            css_fallback="button[type=submit]",
        ),
        "submit_action": SubmitAction(strategy=SubmitStrategy.CLICK_BUTTON),
        "response_locator": BoundLocator(
            strategy=LocatorStrategy.CSS, value=".assistant-message", pick="last"
        ),
    }
    defaults.update(overrides)
    return TargetBinding(**defaults)


class TestLocator:
    def test_role_locator_needs_a_name(self) -> None:
        with pytest.raises(BindingError, match="accessible name"):
            BoundLocator(strategy=LocatorStrategy.ROLE, value="button")

    def test_blank_value_refused(self) -> None:
        with pytest.raises(BindingError, match="cannot be empty"):
            BoundLocator(strategy=LocatorStrategy.CSS, value="  ")

    def test_pick_is_constrained(self) -> None:
        with pytest.raises(BindingError, match="pick must be"):
            BoundLocator(strategy=LocatorStrategy.CSS, value=".x", pick="middle")

    def test_round_trips(self) -> None:
        locator = BoundLocator(
            strategy=LocatorStrategy.ROLE, value="textbox", name="Message"
        )

        assert BoundLocator.from_dict(locator.to_dict()) == locator

    def test_unknown_fields_refused(self) -> None:
        with pytest.raises(BindingError, match="unknown"):
            BoundLocator.from_dict(
                {"strategy": "css", "value": ".x", "evil": "payload"}
            )

    def test_unknown_strategy_refused(self) -> None:
        with pytest.raises(BindingError, match="not supported"):
            BoundLocator.from_dict({"strategy": "xpath", "value": "//x"})


class TestCaptureSettings:
    def test_default_quiet_period_is_generous(self) -> None:
        # A streamed reply commonly pauses ~1s between chunks; a shorter quiet
        # period reports a half-written answer as complete.
        assert CaptureSettings().stable_ms == DEFAULT_STABLE_MS
        assert DEFAULT_STABLE_MS >= 1500

    def test_absurd_values_refused(self) -> None:
        with pytest.raises(BindingError, match="stable_ms"):
            CaptureSettings(stable_ms=10)
        with pytest.raises(BindingError, match="timeout_ms"):
            CaptureSettings(timeout_ms=10)

    def test_non_integer_refused(self) -> None:
        with pytest.raises(BindingError, match="must be an integer"):
            CaptureSettings.from_dict({"stable_ms": "1500"})


class TestSubmitAction:
    def test_click_is_the_default(self) -> None:
        assert SubmitAction().strategy is SubmitStrategy.CLICK_BUTTON

    def test_press_validates_the_key(self) -> None:
        with pytest.raises(ValueError, match="not allowed"):
            SubmitAction(strategy=SubmitStrategy.PRESS_KEY, key="Control+W")

    def test_unknown_strategy_refused(self) -> None:
        with pytest.raises(ValueError, match="not allowed"):
            SubmitAction.from_dict({"strategy": "evaluate"})

    def test_click_maps_to_the_click_operation(self) -> None:
        # The original bug: a button was "submitted" by pressing Enter on it,
        # which does nothing on an ordinary React chat box.
        assert SubmitAction().operation.value == "click"
        assert (
            SubmitAction(strategy=SubmitStrategy.PRESS_KEY).operation.value == "press"
        )


class TestBindingDocument:
    def test_round_trips(self) -> None:
        binding = make_binding()

        restored = TargetBinding.from_dict(binding.to_dict())

        assert restored.target_origin == ORIGIN
        assert restored.submit_action.strategy is SubmitStrategy.CLICK_BUTTON
        assert restored.response_locator.value == ".assistant-message"

    def test_is_json_safe(self) -> None:
        json.dumps(make_binding().to_dict())

    def test_unknown_schema_version_refused(self) -> None:
        document = make_binding().to_dict()
        document["schema_version"] = 99

        with pytest.raises(BindingError, match="not supported"):
            TargetBinding.from_dict(document)

    def test_unknown_top_level_key_refused(self) -> None:
        document = make_binding().to_dict()
        document["cookies"] = {"session": "secret"}

        with pytest.raises(BindingError, match="unknown binding fields"):
            TargetBinding.from_dict(document)

    def test_stores_no_credentials_or_responses(self) -> None:
        # A binding describes page structure. Anything sensitive belongs in the
        # restricted run directory, not in a reusable config file.
        text = json.dumps(make_binding().to_dict()).lower()

        for forbidden in ("cookie", "token", "password", "storage", "response_text"):
            assert forbidden not in text

    def test_fingerprint_is_non_secret_and_describes_the_setup(self) -> None:
        fingerprint = make_binding().fingerprint()

        assert fingerprint["target_origin"] == ORIGIN
        assert "click_button" in str(fingerprint["submit"])

    def test_describe_mentions_all_three_elements(self) -> None:
        described = make_binding().describe()

        assert "input" in described and "submit" in described and "reply" in described


class TestKeys:
    def test_origin_is_normalized_from_a_url(self) -> None:
        assert normalize_origin("http://127.0.0.1:8765/chat?x=1") == ORIGIN

    def test_relative_target_refused(self) -> None:
        with pytest.raises(BindingError, match="absolute http"):
            normalize_origin("/chat")

    def test_key_is_a_safe_filename_stem(self) -> None:
        key = binding_key("https://target.example:8443/chat")

        assert "/" not in key and ":" not in key
        assert key.replace("-", "").replace(".", "").isalnum()

    def test_different_origins_get_different_keys(self) -> None:
        assert binding_key("https://a.example") != binding_key("https://b.example")

    def test_profile_is_part_of_the_key(self) -> None:
        assert binding_key(ORIGIN) != binding_key(ORIGIN, "acme")

    @pytest.mark.parametrize(
        "name", ["../escape", "a/b", "UPPER", "", ".hidden", "x" * 65]
    )
    def test_unsafe_profile_names_refused(self, name: str) -> None:
        with pytest.raises(BindingError, match="not valid"):
            validate_profile(name)


class TestStore:
    def test_save_and_load(self, tmp_path: Path) -> None:
        store = BindingStore(tmp_path)
        binding = make_binding()

        store.save(binding)
        loaded = store.load(ORIGIN)

        assert loaded is not None
        assert loaded.response_locator.value == ".assistant-message"

    def test_missing_binding_returns_none(self, tmp_path: Path) -> None:
        assert BindingStore(tmp_path).load(ORIGIN) is None

    def test_binding_survives_a_new_store_instance(self, tmp_path: Path) -> None:
        # This is the whole point: a clean browser profile must still find a
        # reviewed binding, so it cannot live in browser state.
        BindingStore(tmp_path).save(make_binding())

        assert BindingStore(tmp_path).load(ORIGIN) is not None

    def test_profiles_are_isolated(self, tmp_path: Path) -> None:
        store = BindingStore(tmp_path)
        store.save(make_binding(profile="acme"))

        assert store.load(ORIGIN) is None
        assert store.load(ORIGIN, "acme") is not None

    def test_list_reports_valid_and_invalid(self, tmp_path: Path) -> None:
        store = BindingStore(tmp_path)
        store.save(make_binding())
        (tmp_path / "broken.json").write_text("{not json")

        listed = store.list_bindings()

        assert len(listed) == 2
        assert any(binding is not None for _, binding, _ in listed)
        assert any(error for _, _, error in listed)

    def test_delete(self, tmp_path: Path) -> None:
        store = BindingStore(tmp_path)
        store.save(make_binding())

        assert store.delete(ORIGIN) is True
        assert store.delete(ORIGIN) is False

    def test_corrupt_file_reports_clearly(self, tmp_path: Path) -> None:
        store = BindingStore(tmp_path)
        path = store.path_for(ORIGIN)
        store._ensure_root()
        path.write_text("{ not json")

        with pytest.raises(BindingError, match="not valid JSON"):
            store.load(ORIGIN)

    def test_write_is_atomic_leaving_no_temp_files(self, tmp_path: Path) -> None:
        store = BindingStore(tmp_path)
        store.save(make_binding())

        assert [p.name for p in tmp_path.glob(".tmp-*")] == []

    @POSIX_ONLY
    def test_permissions_are_owner_only(self, tmp_path: Path) -> None:
        store = BindingStore(tmp_path / "bindings")
        path = store.save(make_binding())

        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    @POSIX_ONLY
    def test_symlinked_binding_file_is_refused(self, tmp_path: Path) -> None:
        store = BindingStore(tmp_path)
        store._ensure_root()
        elsewhere = tmp_path / "outside.json"
        elsewhere.write_text("{}")
        os.symlink(elsewhere, store.path_for(ORIGIN))

        with pytest.raises(BindingError, match="symlink"):
            store.load(ORIGIN)

    @POSIX_ONLY
    def test_symlinked_root_is_refused(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        os.symlink(real, link)

        with pytest.raises(BindingError, match="symlink"):
            BindingStore(link).save(make_binding())

    def test_load_path_refuses_a_directory(self, tmp_path: Path) -> None:
        with pytest.raises(BindingError, match="not a regular file"):
            BindingStore(tmp_path).load_path(tmp_path)


class TestVersioning:
    def test_current_version_is_one(self) -> None:
        assert BINDING_SCHEMA_VERSION == 1

    def test_a_future_version_file_is_rejected_not_guessed_at(
        self, tmp_path: Path
    ) -> None:
        store = BindingStore(tmp_path)
        store._ensure_root()
        document = make_binding().to_dict()
        document["schema_version"] = 2
        store.path_for(ORIGIN).write_text(json.dumps(document))

        with pytest.raises(BindingError, match="not supported"):
            store.load(ORIGIN)
