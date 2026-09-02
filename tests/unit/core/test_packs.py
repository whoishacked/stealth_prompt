from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from stealth_prompt.core.packs import PackError, create_pack, parse_pack, read_pack, write_pack
from stealth_prompt.core.strategies import StrategyStore

from .test_strategies import strategy


def signing_key(tmp_path: Path) -> Path:
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("OpenSSL is not installed")
    path = tmp_path / "publisher-private.pem"
    subprocess.run(
        [openssl, "genpkey", "-algorithm", "Ed25519", "-out", str(path)],
        check=True,
        capture_output=True,
    )
    path.chmod(0o600)
    return path


def test_signed_pack_verifies_tampering_fails_and_import_still_needs_preview(
    tmp_path: Path,
) -> None:
    source = StrategyStore(tmp_path / "source.sqlite3")
    source.save_revision(strategy(scope="private_global"))
    pack = create_pack(
        source,
        publisher="Stealth Prompt test publisher",
        private_key=signing_key(tmp_path),
        audience="community",
    )
    path = write_pack(tmp_path / "strategy-pack.json", pack)
    verified = read_pack(path)
    rendered = json.dumps(verified).lower()

    assert verified["signature"]["algorithm"] == "ed25519"
    assert verified["policy"]["minimum_aggregate_attempts"] == 5
    assert verified["snapshot"]["strategies"][0]["scope"] == "private_global"
    for forbidden in (
        "report_id",
        "turn_id",
        "session_id",
        "approved_payload",
        "target_response",
        "origin",
        "canary",
    ):
        assert forbidden not in rendered

    destination = StrategyStore(tmp_path / "destination.sqlite3")
    snapshot, preview = destination.preview_import(verified["snapshot"])
    assert preview["creates"] == 1
    assert destination.capabilities()["private_count"] == 0
    destination.apply_import(snapshot)
    assert destination.capabilities()["private_count"] == 1

    tampered = json.loads(path.read_text())
    tampered["publisher"]["name"] = "Forged publisher"
    with pytest.raises(PackError, match="manifest"):
        parse_pack(tampered)


def test_community_pack_excludes_target_scoped_strategies(tmp_path: Path) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    store.save_revision(strategy(scope="target", scope_key="c" * 64))

    with pytest.raises(PackError, match="no community-eligible"):
        create_pack(
            store,
            publisher="Publisher",
            private_key=signing_key(tmp_path),
            audience="community",
        )


def test_signing_key_permissions_fail_closed(tmp_path: Path) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    store.save_revision(strategy(scope="private_global"))
    key = signing_key(tmp_path)
    key.chmod(0o644)

    with pytest.raises(PackError, match="group or other"):
        create_pack(store, publisher="Publisher", private_key=key)
