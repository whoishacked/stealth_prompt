"""Static checks for the standalone extension's shipped visual assets."""

from __future__ import annotations

import json
import struct
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXTENSION = REPO / "extension"


def png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", data[16:24])


def test_manifest_icons_exist_at_the_declared_sizes() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text())
    icons = manifest["icons"]

    assert manifest["action"]["default_icon"] == icons
    assert set(icons) == {"16", "32", "48", "128"}
    for declared_size, relative_path in icons.items():
        path = EXTENSION / relative_path
        size = int(declared_size)
        assert path.is_file()
        assert png_dimensions(path) == (size, size)
