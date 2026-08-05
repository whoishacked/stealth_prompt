"""Tests for restricted, atomic artifact storage."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from stealth_prompt.workbench.artifacts import (
    ArtifactStore,
    timestamp_slug,
    utc_now,
)

POSIX_ONLY = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="POSIX file modes"
)


class TestNaming:
    def test_unsafe_session_id_refused(self) -> None:
        for bad in ["../escape", "a/b", "", ".hidden", "x" * 65]:
            with pytest.raises(ValueError, match="unsafe session id"):
                ArtifactStore(Path("/tmp"), session_id=bad)

    def test_unsafe_artifact_name_refused(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path, session_id="s-1")

        for bad in ["../escape.json", "a/b.json", "", ".hidden"]:
            with pytest.raises(ValueError, match="unsafe artifact name"):
                store.write_text(bad, "{}")

    def test_timestamp_slug_is_utc_and_sortable(self) -> None:
        slug = timestamp_slug(utc_now())

        stamp, _, suffix = slug.partition("-")
        assert stamp.endswith("Z")
        assert len(stamp) == 16
        assert len(suffix) == 6

    def test_two_slugs_in_the_same_second_do_not_collide(self) -> None:
        # Without the random suffix, two runs started in one second would share
        # a directory and the second would overwrite the first.
        moment = utc_now()

        assert timestamp_slug(moment) != timestamp_slug(moment)


class TestWriting:
    def test_write_and_read_back(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path, session_id="s-1")

        ref = store.write_text("result.json", '{"a":1}')

        assert (store.directory / "result.json").read_text() == '{"a":1}'
        assert ref.relative_path == "s-1/result.json"
        assert ref.size_bytes == 7

    def test_hash_matches_the_content(self, tmp_path: Path) -> None:
        import hashlib

        store = ArtifactStore(tmp_path, session_id="s-1")

        ref = store.write_text("a.json", "hello")

        assert ref.sha256 == hashlib.sha256(b"hello").hexdigest()

    def test_json_round_trips(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path, session_id="s-1")

        store.write_json("doc.json", {"turns": [1, 2], "status": "confirmed"})

        loaded = json.loads((store.directory / "doc.json").read_text())
        assert loaded["status"] == "confirmed"

    def test_rewriting_replaces_rather_than_duplicating_the_ref(
        self, tmp_path: Path
    ) -> None:
        store = ArtifactStore(tmp_path, session_id="s-1")

        store.write_text("a.json", "first")
        store.write_text("a.json", "second")

        assert len(store.refs) == 1
        assert (store.directory / "a.json").read_text() == "second"

    def test_no_temporary_files_are_left_behind(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path, session_id="s-1")

        store.write_text("a.json", "content")

        leftovers = [p.name for p in store.directory.iterdir() if p.name.startswith(".tmp-")]
        assert leftovers == []

    def test_unicode_is_preserved(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path, session_id="s-1")

        store.write_text("a.txt", "café \U0001f600")

        assert (store.directory / "a.txt").read_text(encoding="utf-8") == "café \U0001f600"


class TestPermissions:
    @POSIX_ONLY
    def test_directory_is_owner_only(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path, session_id="s-1")

        store.open()

        mode = stat.S_IMODE(store.directory.stat().st_mode)
        assert mode == 0o700

    @POSIX_ONLY
    def test_files_are_owner_only(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path, session_id="s-1")

        store.write_text("secret.json", "protected")

        mode = stat.S_IMODE((store.directory / "secret.json").stat().st_mode)
        assert mode == 0o600

    @POSIX_ONLY
    def test_no_group_or_other_access(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path, session_id="s-1")
        store.write_text("a.json", "x")

        for path in (store.directory, store.directory / "a.json"):
            mode = stat.S_IMODE(path.stat().st_mode)
            assert not mode & (stat.S_IRWXG | stat.S_IRWXO)


class TestSymlinkSafety:
    @POSIX_ONLY
    def test_refuses_to_write_through_a_symlinked_directory(
        self, tmp_path: Path
    ) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        root = tmp_path / "root"
        root.mkdir()
        os.symlink(elsewhere, root / "s-1")

        store = ArtifactStore(root, session_id="s-1")

        with pytest.raises(ValueError, match="symlink"):
            store.open()

    @POSIX_ONLY
    def test_refuses_to_overwrite_a_symlinked_file(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path, session_id="s-1")
        store.open()
        target = tmp_path / "outside.txt"
        target.write_text("original")
        os.symlink(target, store.directory / "result.json")

        with pytest.raises(ValueError, match="symlink"):
            store.write_text("result.json", "overwritten")

        assert target.read_text() == "original"
