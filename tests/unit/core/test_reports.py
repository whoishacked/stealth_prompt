"""The report library derived from the artifact store.

The index is recomputed from disk on every call, so these tests care most about
what happens when the directory is *not* pristine: a half-written run, a foreign
directory, a symlink, or a name crafted to escape the artifacts root.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stealth_prompt.core.reports import (
    MAX_REPORTS,
    ReportError,
    list_reports,
    resolve_report,
)

VALID_ID = "assistant-20260803T120000Z-abc123"
SECOND_ID = "assistant-20260803T130000Z-def456"


def session_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": 1,
        "kind": "assistant_session",
        "session_id": "session-1",
        "exported_at": "2026-08-03T12:00:00+00:00",
        "verdict": "confirmed",
        "configuration": {
            "origin": "http://127.0.0.1:8765",
            "objective": "instruction_disclosure",
            "effective_model": "fake-1",
            "provider": "fake",
        },
        "turns": [{"turn_id": "t1"}, {"turn_id": "t2"}],
        "timeline": {"events": []},
    }
    document.update(overrides)
    return document


def make_report(
    root: Path, report_id: str = VALID_ID, *, html: bool = True, **overrides: Any
) -> Path:
    directory = root / report_id
    directory.mkdir(parents=True)
    (directory / "session.json").write_text(json.dumps(session_document(**overrides)))
    if html:
        (directory / "report.html").write_text("<!doctype html><title>report</title>")
    return directory


class TestListing:
    def test_an_empty_root_is_an_empty_library(self, tmp_path: Path) -> None:
        """Nothing exported yet is the ordinary first run, not an error."""
        assert list_reports(tmp_path / "results") == []

    def test_it_summarizes_a_stored_session(self, tmp_path: Path) -> None:
        make_report(tmp_path)
        [summary] = list_reports(tmp_path)

        assert summary.report_id == VALID_ID
        assert summary.verdict == "confirmed"
        assert summary.turns == 2
        assert summary.target_origin == "http://127.0.0.1:8765"
        assert summary.objective == "instruction_disclosure"
        assert summary.effective_model == "fake-1"
        assert summary.artifacts == ("report.html", "session.json")

    def test_newest_first(self, tmp_path: Path) -> None:
        make_report(tmp_path, VALID_ID)
        make_report(tmp_path, SECOND_ID)
        assert [s.report_id for s in list_reports(tmp_path)] == [SECOND_ID, VALID_ID]

    def test_a_foreign_directory_is_ignored(self, tmp_path: Path) -> None:
        """The artifacts root may hold the operator's own files."""
        make_report(tmp_path)
        (tmp_path / "notes").mkdir()
        (tmp_path / "notes" / "session.json").write_text("{}")
        assert [s.report_id for s in list_reports(tmp_path)] == [VALID_ID]

    def test_a_run_without_session_json_is_skipped(self, tmp_path: Path) -> None:
        (tmp_path / SECOND_ID).mkdir()
        make_report(tmp_path, VALID_ID)
        assert [s.report_id for s in list_reports(tmp_path)] == [VALID_ID]

    def test_a_corrupt_session_does_not_break_the_library(self, tmp_path: Path) -> None:
        """One bad run must not make every other report unreachable."""
        broken = tmp_path / SECOND_ID
        broken.mkdir()
        (broken / "session.json").write_text("{not json")
        make_report(tmp_path, VALID_ID)

        assert [s.report_id for s in list_reports(tmp_path)] == [VALID_ID]

    def test_a_missing_html_artifact_is_reported_accurately(self, tmp_path: Path) -> None:
        make_report(tmp_path, html=False)
        [summary] = list_reports(tmp_path)
        assert summary.artifacts == ("session.json",)

    def test_the_listing_is_bounded(self, tmp_path: Path) -> None:
        for index in range(6):
            make_report(tmp_path, f"assistant-20260803T12000{index}Z-abc12{index}")
        assert len(list_reports(tmp_path, limit=3)) == 3
        assert len(list_reports(tmp_path)) == 6

    def test_a_symlinked_run_directory_is_skipped(self, tmp_path: Path) -> None:
        """A symlink could point anywhere; the library only reads its own root."""
        outside = tmp_path / "outside"
        make_report(outside, VALID_ID)
        root = tmp_path / "results"
        root.mkdir()
        (root / SECOND_ID).symlink_to(outside / VALID_ID)

        assert list_reports(root) == []


class TestBoundedMetadata:
    def test_untrusted_text_is_truncated_and_flattened(self, tmp_path: Path) -> None:
        """A listing row stays one row even when the origin is hostile."""
        make_report(
            tmp_path,
            configuration={
                "origin": "http://evil\n\nSECOND LINE " + "x" * 500,
                "objective": "y" * 500,
                "effective_model": "z" * 500,
                "provider": "fake",
            },
        )
        [summary] = list_reports(tmp_path)

        assert "\n" not in summary.target_origin
        assert len(summary.target_origin) <= 300
        assert len(summary.objective) <= 80
        assert len(summary.effective_model) <= 120

    def test_no_transcript_reaches_the_summary(self, tmp_path: Path) -> None:
        """The panel gets a row, never the captured target text."""
        make_report(
            tmp_path,
            turns=[
                {"turn_id": "t1", "response": "SECRET_TARGET_TEXT", "approved_payload": "PAYLOAD"}
            ],
        )
        [summary] = list_reports(tmp_path)

        serialized = json.dumps(summary.to_dict())
        assert "SECRET_TARGET_TEXT" not in serialized
        assert "PAYLOAD" not in serialized
        assert summary.turns == 1

    def test_a_missing_verdict_defaults_rather_than_lying(self, tmp_path: Path) -> None:
        make_report(tmp_path, verdict=None)
        [summary] = list_reports(tmp_path)
        assert summary.verdict == "inconclusive"


class TestResolve:
    def test_it_resolves_a_known_artifact(self, tmp_path: Path) -> None:
        make_report(tmp_path)
        path = resolve_report(tmp_path, VALID_ID, "report.html")
        assert path.name == "report.html"
        assert path.is_file()

    @pytest.mark.parametrize(
        "report_id",
        [
            "../../etc",
            "assistant-../../../etc/passwd",
            "..",
            "",
            "assistant-20260803T120000Z-abc123/../../..",
            "results",
        ],
    )
    def test_a_traversal_report_id_is_refused(self, tmp_path: Path, report_id: str) -> None:
        make_report(tmp_path)
        with pytest.raises(ReportError, match="unknown report"):
            resolve_report(tmp_path, report_id, "report.html")

    @pytest.mark.parametrize(
        "artifact", ["../session.json", "/etc/passwd", "notes.txt", "", "report.html.bak"]
    )
    def test_an_unlisted_artifact_is_refused(self, tmp_path: Path, artifact: str) -> None:
        make_report(tmp_path)
        with pytest.raises(ReportError, match="unknown artifact"):
            resolve_report(tmp_path, VALID_ID, artifact)

    def test_a_symlinked_artifact_escaping_the_root_is_refused(self, tmp_path: Path) -> None:
        """The name is legal but the resolved file is outside the root."""
        secret = tmp_path / "secret.html"
        secret.write_text("private")
        root = tmp_path / "results"
        directory = root / VALID_ID
        directory.mkdir(parents=True)
        (directory / "session.json").write_text(json.dumps(session_document()))
        (directory / "report.html").symlink_to(secret)

        with pytest.raises(ReportError, match="unknown report"):
            resolve_report(root, VALID_ID, "report.html")

    def test_a_missing_file_is_refused(self, tmp_path: Path) -> None:
        make_report(tmp_path, html=False)
        with pytest.raises(ReportError, match="unknown report"):
            resolve_report(tmp_path, VALID_ID, "report.html")


def test_max_reports_is_bounded() -> None:
    """A frame cap only helps if the listing itself cannot grow without limit."""
    assert 0 < MAX_REPORTS <= 500
