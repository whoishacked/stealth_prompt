"""The report library, derived from the artifact store rather than duplicated.

Every assistant session already writes ``session.json`` (and usually
``report.html``) into its own directory under the artifacts root. That directory
listing *is* the index, so this module reads it instead of maintaining a second
database that could disagree with the files on disk. A stale index that claims a
report exists after the operator deleted the directory would be worse than no
index at all.

Only bounded metadata crosses to the extension. A session document can contain
the entire captured transcript; the panel needs a row in a list, so it gets a
verdict, a turn count, a model name and an origin, each length-capped. The
report bodies stay on disk and are opened deliberately.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Session directories the Core creates. Anything else in the artifacts root is
#: ignored: the root may legitimately hold unrelated operator files.
SESSION_DIR_PATTERN = re.compile(r"^assistant-\d{8}T\d{6}Z-[0-9a-f]{6}$")

#: Longest listing returned in one frame. A directory with thousands of runs
#: must not become a multi-megabyte frame.
MAX_REPORTS = 200

#: Largest session document to parse while building a listing.
MAX_SESSION_BYTES = 8 * 1024 * 1024

_FIELD_LIMIT = 200


def _bounded(value: object, limit: int = _FIELD_LIMIT) -> str:
    """One line of untrusted text, length-capped.

    Values here originate in a target page or a model reply, so newlines are
    flattened as well as truncated: a listing row must stay one row.
    """
    if value is None:
        return ""
    return " ".join(str(value).split())[:limit]


@dataclass(frozen=True)
class ReportSummary:
    """Bounded metadata for one stored session."""

    report_id: str
    created_at: str
    target_origin: str
    objective: str
    verdict: str
    turns: int
    effective_model: str
    provider: str
    artifacts: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "created_at": self.created_at,
            "target_origin": self.target_origin,
            "objective": self.objective,
            "verdict": self.verdict,
            "turns": self.turns,
            "effective_model": self.effective_model,
            "provider": self.provider,
            "artifacts": list(self.artifacts),
        }


class ReportError(ValueError):
    """A report could not be listed or opened."""


def _summarize(directory: Path) -> ReportSummary | None:
    """Read one session directory, or return None when it is not readable.

    An unreadable or malformed directory is skipped rather than raised: one
    corrupt run must not make the whole library unopenable.
    """
    document_path = directory / "session.json"
    try:
        if not document_path.is_file():
            return None
        if document_path.stat().st_size > MAX_SESSION_BYTES:
            return None
        document = json.loads(document_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(document, dict):
        return None

    configuration = document.get("configuration")
    if not isinstance(configuration, dict):
        configuration = {}

    turns = document.get("turns")
    turn_count = len(turns) if isinstance(turns, list) else 0

    artifacts = tuple(
        name
        for name in ("report.html", "session.json", "scenario.json")
        if (directory / name).is_file()
    )

    return ReportSummary(
        report_id=directory.name,
        # The directory name is the creation stamp; `exported_at` is when the
        # evidence was last written, which is what a reviewer actually wants.
        created_at=_bounded(document.get("exported_at"), 40),
        target_origin=_bounded(configuration.get("origin"), 300),
        objective=_bounded(configuration.get("objective"), 80),
        verdict=_bounded(document.get("verdict"), 40) or "inconclusive",
        turns=turn_count,
        effective_model=_bounded(configuration.get("effective_model"), 120),
        provider=_bounded(configuration.get("provider"), 60),
        artifacts=artifacts,
    )


def list_reports(root: Path, *, limit: int = MAX_REPORTS) -> list[ReportSummary]:
    """List stored sessions, newest first.

    A missing artifacts root is an empty library, not an error: nothing has been
    exported yet is the ordinary first-run state.
    """
    directory = Path(root).expanduser()
    if not directory.is_dir():
        return []
    summaries: list[ReportSummary] = []
    try:
        candidates = sorted(directory.iterdir(), reverse=True)
    except OSError:
        return []
    for entry in candidates:
        if len(summaries) >= max(0, limit):
            break
        # Follow no symlinks out of the artifacts root.
        if entry.is_symlink() or not entry.is_dir():
            continue
        if not SESSION_DIR_PATTERN.match(entry.name):
            continue
        summary = _summarize(entry)
        if summary is not None:
            summaries.append(summary)
    return summaries


def resolve_report(root: Path, report_id: str, artifact: str) -> Path:
    """Resolve one artifact inside one report directory.

    ``report_id`` and ``artifact`` arrive over the socket, so both are matched
    against fixed patterns rather than merely joined. The resolved path is then
    re-checked to be inside the artifacts root, which catches a symlink planted
    in the directory as well as a traversal attempt in the name.
    """
    if not SESSION_DIR_PATTERN.match(report_id or ""):
        raise ReportError("unknown report")
    if artifact not in {"report.html", "session.json", "scenario.json"}:
        raise ReportError("unknown artifact")

    base = Path(root).expanduser().resolve()
    candidate = (base / report_id / artifact).resolve()
    if not candidate.is_file():
        raise ReportError("unknown report")
    if base not in candidate.parents:
        raise ReportError("unknown report")
    return candidate
