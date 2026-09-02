from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path

import pytest

from stealth_prompt.core.attack_state import AttackAttempt, AttackState
from stealth_prompt.core.contracts import Objective
from stealth_prompt.core.strategies import (
    BUILT_INS,
    MAX_PLANNER_STRATEGIES,
    MAX_ROUTER_CANDIDATES,
    StrategyError,
    StrategyStore,
    parse_strategy,
    route_strategies,
)


def strategy(
    *,
    strategy_id: str = "private-structural-probe",
    mechanism: str = "Use a structural test and verify the result.",
    scope: str = "private_global",
    scope_key: str = "",
    response_patterns: list[str] | None = None,
    modes: list[str] | None = None,
    capabilities: list[str] | None = None,
) -> dict[str, object]:
    return {
        "strategy_id": strategy_id,
        "revision": 1,
        "name": "Structural probe",
        "objective_ids": ["instruction_disclosure"],
        "mechanism": mechanism,
        "moves": [
            {
                "move_id": "test_boundary",
                "instruction": "Test the observed boundary once, then inspect the response.",
            }
        ],
        "applicability": {
            "surfaces": ["chat"],
            "capabilities": capabilities if capabilities is not None else [],
            "response_patterns": (
                response_patterns if response_patterns is not None else ["explicit_refusal"]
            ),
            "modes": modes if modes is not None else ["assist", "auto"],
        },
        "prerequisites": ["A relevant boundary was observed."],
        "failure_conditions": ["The same refusal repeats."],
        "initialization": "Begin with the smallest supported boundary test.",
        "scope": scope,
        "scope_key": scope_key,
        "status": "active",
        "source": "digested",
    }


def test_restart_preserves_active_revision_and_owner_only_permissions(tmp_path: Path) -> None:
    path = tmp_path / "private" / "strategies.sqlite3"
    first = StrategyStore(path)
    saved = first.save_revision(strategy())

    reopened = StrategyStore(path)

    assert reopened.open_strategy(saved["strategy_id"])["content_sha256"] == saved["content_sha256"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_custom_parent_directory_permissions_are_not_changed(tmp_path: Path) -> None:
    parent = tmp_path / "existing"
    parent.mkdir(mode=0o755)

    StrategyStore(parent / "strategies.sqlite3")

    assert stat.S_IMODE(parent.stat().st_mode) == 0o755


def test_existing_pre_schema_database_is_backed_up_before_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "strategies.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_marker (value TEXT)")

    store = StrategyStore(path)

    assert path.with_name("strategies.sqlite3.v0.bak").is_file()
    assert store.capabilities()["schema_version"] == 2


def test_version_one_database_adds_scope_keys_without_replacing_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "strategies.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE strategies (
            strategy_id TEXT PRIMARY KEY, active_revision INTEGER NOT NULL,
            status TEXT NOT NULL, scope TEXT NOT NULL, source TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
        )
        connection.execute(
            """CREATE TABLE learning_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
            action TEXT NOT NULL, strategy_id TEXT, revision INTEGER,
            detail_json TEXT NOT NULL)"""
        )
        connection.execute("PRAGMA user_version = 1")

    StrategyStore(path)

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(strategies)")}
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert "scope_key" in columns
    assert version == 3
    assert path.with_name("strategies.sqlite3.v1.bak").is_file()


def test_revisions_are_immutable_and_rollback_moves_only_the_active_pointer(
    tmp_path: Path,
) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    first = store.save_revision(strategy())
    second = store.save_revision(strategy(mechanism="Use a different structural mechanism."))

    assert second["revision"] == 2
    assert [item["revision"] for item in store.revisions(first["strategy_id"])] == [2, 1]

    rolled_back = store.rollback(first["strategy_id"], 1)

    assert rolled_back["revision"] == 1
    assert (
        store.open_strategy(first["strategy_id"], 2)["content_sha256"] == second["content_sha256"]
    )


def test_failed_change_leaves_the_previous_active_revision_intact(tmp_path: Path) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    current = store.save_revision(strategy())

    with pytest.raises(StrategyError, match="unknown revision"):
        store.rollback(current["strategy_id"], 999)

    assert store.open_strategy(current["strategy_id"])["revision"] == current["revision"]


def test_failed_import_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import stealth_prompt.core.strategies as strategy_module

    destination = StrategyStore(tmp_path / "destination.sqlite3")
    current = destination.save_revision(strategy())
    source = StrategyStore(tmp_path / "source.sqlite3")
    source.save_revision(strategy(mechanism="A changed mechanism."))
    source.save_revision(strategy(strategy_id="private-second-strategy"))
    monkeypatch.setattr(strategy_module, "MAX_REVISIONS_PER_STRATEGY", 1)

    with pytest.raises(StrategyError, match="revision quota"):
        destination.apply_import(source.snapshot())

    assert destination.open_strategy(current["strategy_id"])["revision"] == 1
    with pytest.raises(StrategyError, match="unknown strategy"):
        destination.open_strategy("private-second-strategy")


def test_built_in_catalogue_is_visible_but_read_only(tmp_path: Path) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    built_in_id = next(iter(BUILT_INS))

    assert set(BUILT_INS).issubset({item["strategy_id"] for item in store.list_strategies()})
    with pytest.raises(StrategyError, match="read-only"):
        store.set_status(built_in_id, "disabled")
    opened = store.open_strategy(built_in_id)
    opened["moves"].clear()
    assert store.open_strategy(built_in_id)["moves"]


def test_snapshot_hash_is_reproducible_and_round_trips_through_preview(
    tmp_path: Path,
) -> None:
    source = StrategyStore(tmp_path / "source.sqlite3")
    source.save_revision(strategy())

    first = source.snapshot()
    second = source.snapshot()
    exported_path, exported = source.export_snapshot(tmp_path / "exports")

    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert exported["manifest_sha256"] == first["manifest_sha256"]
    assert stat.S_IMODE(exported_path.stat().st_mode) == 0o600

    destination = StrategyStore(tmp_path / "destination.sqlite3")
    normalized, preview = destination.preview_import(json.dumps(first))
    assert preview["creates"] == 1
    imported = destination.apply_import(normalized)

    assert imported[0]["source"] == "imported"
    assert destination.open_strategy(imported[0]["strategy_id"])["revision"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        {"command": "run something"},
        {"selector": "#send"},
        {"api_key": "sk-example-secret-value"},
    ],
)
def test_import_refuses_credentials_and_executable_operation_fields(
    tmp_path: Path, mutation: dict[str, str]
) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    snapshot = store.snapshot()
    snapshot.update(mutation)

    with pytest.raises(StrategyError, match="credential or executable operation"):
        store.preview_import(snapshot)


def test_strategy_text_refuses_a_credential_value() -> None:
    document = strategy(mechanism="Use api_key=sk-this-is-a-secret-value in the request.")

    with pytest.raises(StrategyError, match="credential"):
        parse_strategy(document)


def test_router_applies_scope_hierarchy_and_fixed_context_budget(tmp_path: Path) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    target_key = "a" * 64
    project_key = "b" * 64
    for strategy_id, scope, scope_key in (
        ("private-target-probe", "target", target_key),
        ("private-project-probe", "project", project_key),
        ("private-global-probe", "private_global", ""),
    ):
        store.save_revision(
            strategy(
                strategy_id=strategy_id,
                scope=scope,
                scope_key=scope_key,
                response_patterns=[],
            )
        )

    route = route_strategies(
        store,
        objective=Objective.INSTRUCTION_DISCLOSURE,
        mode="assist",
        state=AttackState(),
        target_scope_key=target_key,
        project_scope_key=project_key,
    )

    assert route.candidate_ids[:3] == (
        "private-target-probe",
        "private-project-probe",
        "private-global-probe",
    )
    assert len(route.candidates) <= MAX_ROUTER_CANDIDATES
    assert len(route.planner_strategies) <= MAX_PLANNER_STRATEGIES
    assert len(route.prompt_context()) <= 4_000


def test_router_never_offers_disabled_incompatible_or_exhausted_entries(
    tmp_path: Path,
) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    store.save_revision(
        strategy(strategy_id="private-exhausted", response_patterns=[])
    )
    disabled = store.save_revision(
        strategy(strategy_id="private-disabled", response_patterns=[])
    )
    store.set_status(disabled["strategy_id"], "disabled")
    store.save_revision(
        strategy(
            strategy_id="private-wrong-mode",
            modes=["auto"],
            response_patterns=[],
        )
    )
    store.save_revision(
        strategy(
            strategy_id="private-wrong-capability",
            capabilities=["send_email"],
            response_patterns=[],
        )
    )
    state = AttackState.build(
        [
            AttackAttempt(
                turn_id=f"turn-{index}",
                turn=index,
                strategy_id="private-exhausted",
                move_id="test_boundary",
                failure_signature="explicit_refusal",
            )
            for index in (1, 2)
        ]
    )

    route = route_strategies(
        store,
        objective=Objective.INSTRUCTION_DISCLOSURE,
        mode="assist",
        state=state,
    )

    assert not {
        "private-exhausted",
        "private-disabled",
        "private-wrong-mode",
        "private-wrong-capability",
    }.intersection(route.candidate_ids)


def test_router_uses_derived_experience_counts_without_an_aggregate_table(
    tmp_path: Path,
) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    for strategy_id in ("private-a-probe", "private-z-probe"):
        store.save_revision(strategy(strategy_id=strategy_id, response_patterns=[]))
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """INSERT INTO experiences
            (experience_id,report_id,turn_id,objective_id,strategy_id,outcome,
             deterministic,document_json,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "experience-1",
                "report-1",
                "turn-1",
                "instruction_disclosure",
                "private-z-probe",
                "confirmed",
                1,
                "{}",
                "2026-08-14T00:00:00+00:00",
            ),
        )

    route = route_strategies(
        store,
        objective=Objective.INSTRUCTION_DISCLOSURE,
        mode="assist",
        state=AttackState(),
    )

    assert route.candidate_ids[:2] == ("private-z-probe", "private-a-probe")


def test_status_filter_and_revision_history_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "strategies.sqlite3"
    store = StrategyStore(path)
    saved = store.save_revision(strategy())
    store.set_status(saved["strategy_id"], "disabled")

    reopened = StrategyStore(path)

    assert [item["strategy_id"] for item in reopened.list_strategies(status="disabled")] == [
        saved["strategy_id"]
    ]
    assert reopened.revisions(saved["strategy_id"])[0]["revision"] == 1
