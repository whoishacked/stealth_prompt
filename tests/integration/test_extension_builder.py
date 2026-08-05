"""Tests for the per-session extension rendering.

The rendered directory holds the broker token and decides which origins the
dock can be injected into, so both are asserted directly.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from stealth_prompt.workbench.broker import (
    EXTENSION_ID,
    EXTENSION_ORIGIN,
    EXTENSION_PUBLIC_KEY,
)
from stealth_prompt.workbench.config import WorkbenchConfig
from stealth_prompt.workbench.extension_builder import build_extension

POSIX_ONLY = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX modes")

LOCAL = "http://127.0.0.1:8765/chat"


@pytest.fixture
def built(tmp_path: Path):
    config = WorkbenchConfig(target_url=LOCAL)
    extension = build_extension(
        config,
        broker_url="ws://127.0.0.1:54321/ws",
        public_key=EXTENSION_PUBLIC_KEY,
        extension_id=EXTENSION_ID,
        parent=tmp_path,
    )
    try:
        yield extension, config
    finally:
        extension.cleanup()


def manifest_of(extension) -> dict:
    return json.loads((extension.directory / "manifest.json").read_text())


class TestManifest:
    def test_is_manifest_v3(self, built) -> None:
        extension, _ = built

        assert manifest_of(extension)["manifest_version"] == 3

    def test_host_permissions_are_pinned_to_the_target_origin(self, built) -> None:
        extension, config = built

        assert manifest_of(extension)["host_permissions"] == [
            f"{config.target_origin}/*"
        ]

    def test_content_script_matches_only_the_target_origin(self, built) -> None:
        extension, config = built

        scripts = manifest_of(extension)["content_scripts"]
        assert len(scripts) == 1
        assert scripts[0]["matches"] == [f"{config.target_origin}/*"]

    def test_all_urls_is_never_present(self, built) -> None:
        extension, _ = built

        assert "<all_urls>" not in (extension.directory / "manifest.json").read_text()

    def test_a_different_target_yields_different_permissions(self, tmp_path: Path) -> None:
        config = WorkbenchConfig(
            target_url="https://other.example:8443/chat",
            authorization_acknowledged=True,
        )
        extension = build_extension(
            config,
            broker_url="ws://127.0.0.1:1/ws",
            public_key=EXTENSION_PUBLIC_KEY,
            extension_id=EXTENSION_ID,
            parent=tmp_path,
        )
        try:
            assert manifest_of(extension)["host_permissions"] == [
                "https://other.example:8443/*"
            ]
        finally:
            extension.cleanup()

    def test_public_key_fixes_the_extension_id(self, built) -> None:
        extension, _ = built

        assert manifest_of(extension)["key"] == EXTENSION_PUBLIC_KEY
        assert extension.origin == EXTENSION_ORIGIN

    def test_extension_id_matches_the_key(self, built) -> None:
        # Chromium derives the ID from SHA-256 of the DER public key, mapping
        # each nibble to a..p. If this drifts, Origin validation breaks.
        import base64
        import hashlib

        der = base64.b64decode(EXTENSION_PUBLIC_KEY)
        digest = hashlib.sha256(der).digest()[:16]
        derived = "".join(
            chr(ord("a") + (b >> 4)) + chr(ord("a") + (b & 0xF)) for b in digest
        )

        assert derived == EXTENSION_ID

    def test_content_security_policy_forbids_remote_script(self, built) -> None:
        extension, _ = built

        csp = manifest_of(extension)["content_security_policy"]["extension_pages"]
        assert "script-src 'self'" in csp
        assert "object-src 'none'" in csp


class TestConfigFile:
    def test_carries_the_broker_url_and_token(self, built) -> None:
        extension, config = built

        content = (extension.directory / "config.js").read_text()
        assert "ws://127.0.0.1:54321/ws" in content
        assert config.broker.token in content

    def test_is_valid_javascript_assignment(self, built) -> None:
        extension, _ = built

        content = (extension.directory / "config.js").read_text()
        assert content.startswith("const WB_CONFIG = {")
        # The object body must be valid JSON so no escaping bug can inject code.
        body = content[len("const WB_CONFIG = ") : content.rindex("}") + 1]
        json.loads(body)


class TestPackaging:
    def test_all_assets_are_local(self, built) -> None:
        extension, _ = built

        names = {p.name for p in extension.directory.iterdir()}
        assert names == {"manifest.json", "background.js", "content.js", "config.js"}

    def test_no_remote_urls_in_shipped_scripts(self, built) -> None:
        extension, _ = built

        for name in ("background.js", "content.js"):
            content = (extension.directory / name).read_text()
            assert "http://" not in content.replace("http://127.0.0.1", "")
            assert "https://" not in content

    def test_content_script_uses_no_dynamic_code_execution(self, built) -> None:
        extension, _ = built

        content = (extension.directory / "content.js").read_text()
        for forbidden in ("eval(", "new Function(", "innerHTML = payload", "setTimeout(\""):
            assert forbidden not in content

    def test_dock_uses_a_closed_shadow_root(self, built) -> None:
        extension, _ = built

        content = (extension.directory / "content.js").read_text()
        assert "attachShadow({ mode: 'closed' })" in content

    @POSIX_ONLY
    def test_directory_and_files_are_owner_only(self, built) -> None:
        extension, _ = built

        assert stat.S_IMODE(extension.directory.stat().st_mode) == 0o700
        for child in extension.directory.iterdir():
            assert stat.S_IMODE(child.stat().st_mode) == 0o600

    def test_cleanup_removes_the_token_bearing_directory(self, tmp_path: Path) -> None:
        config = WorkbenchConfig(target_url=LOCAL)
        extension = build_extension(
            config,
            broker_url="ws://127.0.0.1:1/ws",
            public_key=EXTENSION_PUBLIC_KEY,
            extension_id=EXTENSION_ID,
            parent=tmp_path,
        )

        extension.cleanup()

        assert not extension.directory.exists()
