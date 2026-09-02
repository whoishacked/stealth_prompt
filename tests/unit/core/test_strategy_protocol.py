from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from stealth_prompt.core.server import CoreServer, decode
from stealth_prompt.core.strategies import StrategyStore

from .test_learning import REPORT_ID, report_document
from .test_strategies import strategy


def dispatch(server: CoreServer, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    replies: list[dict[str, Any]] = []

    async def send(raw: str) -> None:
        replies.append(json.loads(raw))

    asyncio.run(server.dispatch(kind, payload, send))
    assert len(replies) == 1
    return replies[0]


def test_learning_capabilities_and_built_ins_are_available(tmp_path: Path) -> None:
    server = CoreServer(
        artifacts_root=tmp_path / "results",
        strategy_db=tmp_path / "strategies.sqlite3",
    )

    result = dispatch(server, "learning.capabilities", {})
    listed = dispatch(server, "strategies.list", {"status": "active"})

    assert result["type"] == "learning.capabilities"
    assert result["payload"]["available"] is True
    assert result["payload"]["built_in_count"] >= 1
    assert any(item["scope"] == "built_in" for item in listed["payload"]["strategies"])


def test_import_requires_preview_token_and_applies_once(tmp_path: Path) -> None:
    source = StrategyStore(tmp_path / "source.sqlite3")
    source.save_revision(strategy())
    server = CoreServer(
        artifacts_root=tmp_path / "results",
        strategy_db=tmp_path / "destination.sqlite3",
    )

    preview = dispatch(server, "strategies.import_preview", {"snapshot": source.snapshot()})
    token = preview["payload"]["preview_token"]
    applied = dispatch(server, "strategies.import_apply", {"preview_token": token})
    listed = dispatch(server, "strategies.list", {})

    assert preview["payload"]["preview"]["creates"] == 1
    assert applied["type"] == "strategies.imported"
    assert applied["payload"]["strategies"][0]["strategy_id"] == ("private-structural-probe")
    assert any(
        item["strategy_id"] == "private-structural-probe"
        for item in listed["payload"]["strategies"]
    )
    assert token not in server._strategy_import_previews  # noqa: SLF001


def test_extension_protocol_accepts_strategy_library_frames() -> None:
    assert decode(
        json.dumps(
            {
                "protocol_version": 1,
                "type": "strategies.open",
                "payload": {"strategy_id": "builtin-capability-mapping"},
            }
        )
    ) == (
        "strategies.open",
        {"strategy_id": "builtin-capability-mapping"},
    )


def test_report_digest_requires_review_and_applies_only_once(tmp_path: Path) -> None:
    root = tmp_path / "results"
    directory = root / REPORT_ID
    directory.mkdir(parents=True)
    (directory / "session.json").write_text(json.dumps(report_document()))
    server = CoreServer(
        artifacts_root=root,
        strategy_db=tmp_path / "strategies.sqlite3",
    )

    preview = dispatch(server, "learning.preview", {"report_id": REPORT_ID})
    before = dispatch(server, "strategies.list", {})
    applied = dispatch(
        server,
        "learning.apply",
        {
            "preview_token": preview["payload"]["preview_token"],
            "decision": "accept",
        },
    )
    repeated = dispatch(server, "learning.preview", {"report_id": REPORT_ID})

    assert preview["payload"]["action"] == "create"
    assert len(before["payload"]["strategies"]) == 3  # built-ins only
    assert applied["payload"]["experience_stored"] is True
    assert repeated["payload"]["eligibility"]["eligible"] is False
    assert "already has an accepted digest" in repeated["payload"]["eligibility"]["reason"]
