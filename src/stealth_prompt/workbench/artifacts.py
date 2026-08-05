"""Restricted, atomic artifact storage for a workbench session.

A session directory can contain a target's system prompt, retrieved documents,
or another user's data. It is therefore created ``0700`` with ``0600`` files,
written atomically so a crash cannot leave a half-written transcript, and never
placed by following a symlink.

Permissions are best effort: on Windows the mode arguments are ignored by the
platform, which is documented rather than silently assumed away.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DIR_MODE = 0o700
FILE_MODE = 0o600

#: Generated names only. Target-supplied text never becomes a path component.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: Ceiling on one session directory. A runaway target response should fail the
#: write loudly rather than fill the disk.
DEFAULT_QUOTA_BYTES = 64 * 1024 * 1024


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp_slug(moment: datetime | None = None) -> str:
    """A sortable UTC stamp plus random suffix.

    The suffix is not decoration: two runs started in the same second would
    otherwise share a directory and the second would overwrite the first.
    """
    stamp = (moment or utc_now()).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


@dataclass(frozen=True)
class ArtifactRef:
    """A stored artifact, referenced by relative path and content hash."""

    name: str
    relative_path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


class ArtifactStore:
    """Owns one session directory and everything written into it."""

    def __init__(
        self,
        root: Path,
        *,
        session_id: str,
        max_total_bytes: int = DEFAULT_QUOTA_BYTES,
    ) -> None:
        if not _SAFE_NAME.match(session_id):
            raise ValueError(f"unsafe session id {session_id!r}")
        self._root = Path(root).expanduser()
        self._session_id = session_id
        self._dir = self._root / session_id
        self._refs: list[ArtifactRef] = []
        self._max_total_bytes = max_total_bytes
        self._written_bytes = 0

    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def refs(self) -> tuple[ArtifactRef, ...]:
        return tuple(self._refs)

    def open(self) -> Path:
        """Create the session directory with restrictive permissions.

        The root and every intermediate directory are checked first: a symlinked
        or world-writable results root would let another local user read or
        replace evidence.
        """
        self._check_root()
        self._root.mkdir(parents=True, exist_ok=True)
        if self._dir.is_symlink():
            raise ValueError(f"refusing to write through symlink {self._dir}")
        self._dir.mkdir(mode=DIR_MODE, exist_ok=True)
        _chmod(self._dir, DIR_MODE)
        return self._dir

    def _check_root(self) -> None:
        """Refuse an unsafe results root."""
        probe = self._root
        seen: set[Path] = set()
        while True:
            if probe in seen:  # pragma: no cover - defensive
                break
            seen.add(probe)
            if probe.is_symlink():
                raise ValueError(f"refusing to use symlinked results path {probe}")
            if probe.exists():
                if not probe.is_dir():
                    raise ValueError(f"results path {probe} is not a directory")
                mode = stat.S_IMODE(probe.stat().st_mode)
                # A world-writable ancestor means anyone local can swap a
                # directory under us between the check and the write.
                if mode & stat.S_IWOTH and not (mode & stat.S_ISVTX):
                    raise ValueError(
                        f"refusing to write into world-writable directory {probe}"
                    )
                break
            parent = probe.parent
            if parent == probe:
                break
            probe = parent

    def write_text(self, name: str, content: str) -> ArtifactRef:
        """Write ``content`` atomically as ``name`` inside the session directory."""
        if not _SAFE_NAME.match(name):
            raise ValueError(f"unsafe artifact name {name!r}")
        self.open()
        target = self._dir / name
        if target.is_symlink():
            raise ValueError(f"refusing to overwrite symlink {target}")

        encoded = content.encode("utf-8")
        if self._written_bytes + len(encoded) > self._max_total_bytes:
            raise ValueError(
                f"artifact quota of {self._max_total_bytes} bytes exceeded"
            )
        # Write to a sibling temp file, then rename: a reader never observes a
        # partial artifact, and the final name appears atomically.
        handle, temp_name = tempfile.mkstemp(dir=self._dir, prefix=".tmp-")
        temp_path = Path(temp_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            _chmod(temp_path, FILE_MODE)
            os.replace(temp_path, target)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

        ref = ArtifactRef(
            name=name,
            relative_path=f"{self._session_id}/{name}",
            sha256=hashlib.sha256(encoded).hexdigest(),
            size_bytes=len(encoded),
        )
        self._refs = [existing for existing in self._refs if existing.name != name]
        self._refs.append(ref)
        self._written_bytes += len(encoded)
        return ref

    def write_json(self, name: str, document: Any) -> ArtifactRef:
        """Write ``document`` as pretty-printed, deterministic JSON."""
        return self.write_text(
            name, json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False)
        )


def _chmod(path: Path, mode: int) -> None:
    """Apply ``mode`` where the platform supports it."""
    try:
        path.chmod(mode)
    except (NotImplementedError, PermissionError, OSError):
        # Windows and some network filesystems do not implement POSIX modes.
        # The limitation is documented; failing the run would be worse.
        pass
