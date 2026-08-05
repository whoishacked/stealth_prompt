"""Renders the per-session unpacked extension.

The extension is generated rather than shipped ready-to-load because two of its
values are session-specific and security-relevant:

* ``host_permissions`` and the content-script ``matches`` are pinned to the one
  target origin chosen for this session, so the dock cannot be injected into any
  other site the operator happens to visit;
* ``config.js`` carries the one-time broker token.

The rendered directory is therefore treated as secret-bearing: ``0700`` with
``0600`` files, created fresh per session and removed at the end.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .artifacts import DIR_MODE, FILE_MODE, _chmod
from .config import WorkbenchConfig

TEMPLATE_DIR = Path(__file__).parent / "extension"
STATIC_FILES = ("background.js", "content.js")


@dataclass(frozen=True)
class BuiltExtension:
    """A rendered extension directory."""

    directory: Path
    extension_id: str
    origin: str

    def cleanup(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)


def _render_manifest(target_origin: str, public_key: str) -> str:
    template = (TEMPLATE_DIR / "manifest.json.tmpl").read_text(encoding="utf-8")
    rendered = template.replace("__EXTENSION_KEY__", public_key).replace(
        "__TARGET_ORIGIN__", target_origin
    )
    # Parse it back: a manifest that is not valid JSON would fail opaquely
    # inside Chromium, and the origin substitution must not have broken it.
    parsed = json.loads(rendered)
    permissions = parsed["host_permissions"]
    if permissions != [f"{target_origin}/*"]:
        raise ValueError("manifest host permissions were not pinned to the target origin")
    if "<all_urls>" in json.dumps(parsed):
        raise ValueError("refusing to build an extension with <all_urls> permissions")
    return rendered


def _render_config(config: WorkbenchConfig, broker_url: str) -> str:
    document = {
        "brokerUrl": broker_url,
        "token": config.broker.token,
        "targetOrigin": config.target_origin,
        "maxMessageBytes": config.broker.max_message_bytes,
    }
    return f"const WB_CONFIG = {json.dumps(document, indent=2)};\n"


def build_extension(
    config: WorkbenchConfig,
    *,
    broker_url: str,
    public_key: str,
    extension_id: str,
    parent: Path | None = None,
) -> BuiltExtension:
    """Render the extension for one session and return its directory."""
    directory = Path(
        tempfile.mkdtemp(prefix="stealth-prompt-ext-", dir=str(parent) if parent else None)
    )
    _chmod(directory, DIR_MODE)

    (directory / "manifest.json").write_text(
        _render_manifest(config.target_origin, public_key), encoding="utf-8"
    )
    for name in STATIC_FILES:
        shutil.copyfile(TEMPLATE_DIR / name, directory / name)
    (directory / "config.js").write_text(
        _render_config(config, broker_url), encoding="utf-8"
    )

    for child in directory.iterdir():
        _chmod(child, FILE_MODE)

    return BuiltExtension(
        directory=directory,
        extension_id=extension_id,
        origin=f"chrome-extension://{extension_id}",
    )
