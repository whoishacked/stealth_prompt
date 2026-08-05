"""Tests for workbench configuration and its safety defaults.

Each default that protects the operator has a test asserting it, so relaxing one
becomes a visible, deliberate diff rather than a quiet change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stealth_prompt.agents import AgentKind
from stealth_prompt.workbench.config import (
    MAX_MESSAGE_BYTES_CEILING,
    BrokerSettings,
    BrowserSettings,
    ProfileMode,
    SafetySettings,
    TargetDataSharing,
    WorkbenchConfig,
    WorkbenchConfigError,
    build_workbench_config,
    is_loopback_url,
    target_origin_of,
    validate_profile_name,
)

LOCAL_TARGET = "http://127.0.0.1:8765/chat"
REMOTE_TARGET = "https://authorized-target.example/chat"


class TestTargetUrl:
    @pytest.mark.parametrize("url", ["ftp://host/x", "file:///etc/passwd", "notaurl"])
    def test_non_http_schemes_rejected(self, url: str) -> None:
        with pytest.raises(WorkbenchConfigError, match="must use http or https"):
            WorkbenchConfig(target_url=url)

    def test_url_without_a_host_rejected(self) -> None:
        with pytest.raises(WorkbenchConfigError, match="has no host"):
            WorkbenchConfig(target_url="http:///justapath")

    @pytest.mark.parametrize(
        ("url", "origin"),
        [
            ("https://example.test/chat", "https://example.test"),
            ("https://example.test:8443/chat?a=1", "https://example.test:8443"),
            ("http://127.0.0.1:8765/chat", "http://127.0.0.1:8765"),
        ],
    )
    def test_origin_drops_path_query_and_keeps_port(self, url: str, origin: str) -> None:
        assert target_origin_of(url) == origin
        assert WorkbenchConfig(target_url=url).target_origin == origin

    @pytest.mark.parametrize(
        "url", ["http://127.0.0.1:8765/", "http://localhost:3000/", "http://[::1]:9/"]
    )
    def test_loopback_targets_detected(self, url: str) -> None:
        assert is_loopback_url(url) is True

    @pytest.mark.parametrize(
        "url", ["https://example.test/", "http://10.0.0.5/", "http://192.168.1.9/"]
    )
    def test_non_loopback_targets_detected(self, url: str) -> None:
        # Private addresses are legitimate authorized targets, but they still
        # require acknowledgement because they are not this machine.
        assert is_loopback_url(url) is False


class TestProfile:
    def test_default_profile_is_ephemeral(self) -> None:
        browser = BrowserSettings()

        assert browser.mode is ProfileMode.EPHEMERAL
        assert browser.profile_name is None
        assert browser.profile_dir is None

    def test_named_profile_is_persistent(self, tmp_path: Path) -> None:
        browser = BrowserSettings(profile_name="acme-q3", profile_root=tmp_path)

        assert browser.mode is ProfileMode.PERSISTENT
        assert browser.profile_dir == (tmp_path / "acme-q3").resolve()

    @pytest.mark.parametrize(
        "name", ["acme", "acme-q3", "a1", "acme.test_1", "x" * 64]
    )
    def test_valid_profile_names(self, name: str) -> None:
        assert validate_profile_name(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "..",
            "../escape",
            "a/b",
            "a\\b",
            ".hidden",
            "-leading",
            "UPPER",
            "with space",
            "x" * 65,
            "name\x00null",
        ],
    )
    def test_unsafe_profile_names_rejected(self, name: str) -> None:
        with pytest.raises(WorkbenchConfigError, match="is not valid"):
            validate_profile_name(name)

    def test_traversal_cannot_escape_the_profile_root(self, tmp_path: Path) -> None:
        # Belt and braces: the name pattern already forbids separators, so this
        # asserts the pattern is what stops it.
        with pytest.raises(WorkbenchConfigError):
            BrowserSettings(profile_name="../../etc", profile_root=tmp_path)

    def test_there_is_no_option_to_attach_to_an_existing_browser(self) -> None:
        # The legacy Selenium path could attach to the operator's own Chrome.
        # The workbench must not grow that capability back.
        fields = set(BrowserSettings.__dataclass_fields__)
        for forbidden in (
            "connect_to_existing",
            "remote_debugging_port",
            "debugger_address",
            "user_data_dir",
            "executable_path",
            "channel",
        ):
            assert forbidden not in fields


class TestBrowserDefaults:
    def test_sandbox_on_and_tls_verified_by_default(self) -> None:
        browser = BrowserSettings()

        assert browser.sandbox is True
        assert browser.ignore_https_errors is False

    def test_headed_by_default_so_the_dock_is_visible(self) -> None:
        assert BrowserSettings().headless is False

    def test_tiny_viewport_rejected(self) -> None:
        with pytest.raises(WorkbenchConfigError, match="at least 320x240"):
            BrowserSettings(viewport_width=100, viewport_height=100)


class TestBroker:
    def test_binds_loopback_on_a_random_port_by_default(self) -> None:
        broker = BrokerSettings()

        assert broker.host == "127.0.0.1"
        assert broker.port == 0

    @pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5", "localhost"])
    def test_non_loopback_bind_refused(self, host: str) -> None:
        with pytest.raises(WorkbenchConfigError, match="refusing to bind"):
            BrokerSettings(host=host)

    def test_token_is_generated_and_unguessable(self) -> None:
        first, second = BrokerSettings(), BrokerSettings()

        assert first.token != second.token
        assert len(first.token) >= 32

    def test_short_token_refused(self) -> None:
        with pytest.raises(WorkbenchConfigError, match="too short to be unguessable"):
            BrokerSettings(token="abc")

    def test_token_is_absent_from_repr(self) -> None:
        broker = BrokerSettings()

        assert broker.token not in repr(broker)

    @pytest.mark.parametrize("size", [0, -1, MAX_MESSAGE_BYTES_CEILING + 1])
    def test_message_size_bounds_enforced(self, size: int) -> None:
        with pytest.raises(WorkbenchConfigError, match="max_message_bytes"):
            BrokerSettings(max_message_bytes=size)

    def test_only_extension_origins_may_be_trusted(self) -> None:
        with pytest.raises(WorkbenchConfigError, match="chrome-extension"):
            BrokerSettings(allowed_origins=("https://evil.test",))

    def test_valid_extension_origin_accepted(self) -> None:
        origin = "chrome-extension://" + "a" * 32

        broker = BrokerSettings(allowed_origins=(origin,))

        assert broker.allowed_origins == (origin,)

    def test_with_origin_adds_without_duplicating(self) -> None:
        origin = "chrome-extension://" + "b" * 32
        broker = BrokerSettings()

        once = broker.with_origin(origin)
        twice = once.with_origin(origin)

        assert once.allowed_origins == (origin,)
        assert twice is once
        assert once.token == broker.token


class TestSafetyDefaults:
    def test_target_data_sharing_defaults_to_none(self) -> None:
        assert SafetySettings().target_data_sharing is TargetDataSharing.NONE

    def test_send_approval_required_by_default(self) -> None:
        assert SafetySettings().require_send_approval is True

    def test_limits_are_positive(self) -> None:
        safety = SafetySettings()

        assert safety.max_payload_bytes > 0
        assert safety.max_response_bytes > 0
        assert safety.max_turns >= 1

    def test_non_positive_byte_limit_rejected(self) -> None:
        with pytest.raises(WorkbenchConfigError, match="byte limits must be positive"):
            SafetySettings(max_payload_bytes=0)

    def test_invalid_redaction_pattern_reported_clearly(self) -> None:
        with pytest.raises(WorkbenchConfigError, match="does not compile"):
            SafetySettings(redact_patterns=("[unclosed",))

    def test_valid_redaction_pattern_accepted(self) -> None:
        assert SafetySettings(redact_patterns=(r"\bsecret-\w+",)).redact_patterns


class TestPreflight:
    def test_loopback_target_needs_no_acknowledgement(self) -> None:
        config = WorkbenchConfig(target_url=LOCAL_TARGET)

        assert config.requires_acknowledgement is False
        assert config.preflight_problems() == ()

    def test_remote_target_blocks_until_acknowledged(self) -> None:
        config = WorkbenchConfig(target_url=REMOTE_TARGET)

        problems = config.preflight_problems()

        assert len(problems) == 1
        assert "--i-am-authorized" in problems[0]
        assert config.target_origin in problems[0]

    def test_acknowledged_remote_target_passes(self) -> None:
        config = WorkbenchConfig(
            target_url=REMOTE_TARGET, authorization_acknowledged=True
        )

        assert config.preflight_problems() == ()

    def test_sharing_target_data_with_the_fake_agent_is_refused(self) -> None:
        config = WorkbenchConfig(
            target_url=LOCAL_TARGET,
            safety=SafetySettings(target_data_sharing=TargetDataSharing.FULL),
        )

        assert any("only meaningful with a real agent" in p for p in config.preflight_problems())


class TestWarnings:
    def test_quiet_when_every_default_holds(self) -> None:
        assert WorkbenchConfig(target_url=LOCAL_TARGET).warnings() == ()

    def test_disabled_tls_is_announced(self) -> None:
        config = WorkbenchConfig(
            target_url=LOCAL_TARGET, browser=BrowserSettings(ignore_https_errors=True)
        )

        assert any("TLS verification is DISABLED" in w for w in config.warnings())

    def test_disabled_sandbox_is_announced(self) -> None:
        config = WorkbenchConfig(
            target_url=LOCAL_TARGET, browser=BrowserSettings(sandbox=False)
        )

        assert any("sandbox is DISABLED" in w for w in config.warnings())

    def test_persistent_profile_is_announced(self, tmp_path: Path) -> None:
        config = WorkbenchConfig(
            target_url=LOCAL_TARGET,
            browser=BrowserSettings(profile_name="acme", profile_root=tmp_path),
        )

        assert any("persistent profile" in w for w in config.warnings())

    def test_full_sharing_is_announced(self) -> None:
        config = WorkbenchConfig(
            target_url=LOCAL_TARGET,
            safety=SafetySettings(target_data_sharing=TargetDataSharing.FULL),
        )

        assert any("verbatim" in w for w in config.warnings())

    def test_unattended_sending_is_announced(self) -> None:
        config = WorkbenchConfig(
            target_url=LOCAL_TARGET,
            safety=SafetySettings(require_send_approval=False),
        )

        assert any("WITHOUT per-send approval" in w for w in config.warnings())


class TestDescribe:
    def test_snapshot_never_contains_the_broker_token(self) -> None:
        config = WorkbenchConfig(target_url=LOCAL_TARGET)

        rendered = repr(config.describe())

        assert config.broker.token not in rendered

    def test_snapshot_records_the_security_relevant_settings(self) -> None:
        config = WorkbenchConfig(target_url=LOCAL_TARGET)

        described = config.describe()

        for key in (
            "target_origin",
            "target_data_sharing",
            "browser_sandbox",
            "browser_ignore_https_errors",
            "browser_profile_mode",
            "require_send_approval",
            "authorization_acknowledged",
        ):
            assert key in described

    def test_snapshot_is_json_safe(self) -> None:
        import json

        json.dumps(WorkbenchConfig(target_url=LOCAL_TARGET).describe())


class TestBuildWorkbenchConfig:
    def test_defaults_are_the_safe_ones(self) -> None:
        config = build_workbench_config(target_url=LOCAL_TARGET)

        assert config.agent.kind is AgentKind.FAKE
        assert config.safety.target_data_sharing is TargetDataSharing.NONE
        assert config.browser.mode is ProfileMode.EPHEMERAL
        assert config.browser.sandbox is True
        assert config.broker.host == "127.0.0.1"

    def test_unknown_agent_lists_the_known_ones(self) -> None:
        with pytest.raises(WorkbenchConfigError) as excinfo:
            build_workbench_config(target_url=LOCAL_TARGET, agent="gpt-9")

        assert "claude" in str(excinfo.value)

    def test_unknown_sharing_mode_lists_the_known_ones(self) -> None:
        with pytest.raises(WorkbenchConfigError) as excinfo:
            build_workbench_config(target_url=LOCAL_TARGET, target_data_sharing="some")

        assert "redacted" in str(excinfo.value)

    def test_invalid_limit_is_reported_as_a_config_error(self) -> None:
        with pytest.raises(WorkbenchConfigError, match="max_turns"):
            build_workbench_config(target_url=LOCAL_TARGET, max_turns=0)

    def test_profile_flows_through_to_the_browser(self, tmp_path: Path) -> None:
        config = build_workbench_config(target_url=LOCAL_TARGET, profile="acme-q3")

        assert config.browser.profile_name == "acme-q3"
        assert config.browser.mode is ProfileMode.PERSISTENT
