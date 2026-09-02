from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from stealth_prompt.core.attack_state import AttackState
from stealth_prompt.core.contracts import Objective
from stealth_prompt.core.evaluation import (
    EvaluationError,
    evaluate_frozen,
    load_benchmark,
    measurement_from_report,
    measurements_document,
    promotion_preview,
    routing_snapshot_sha256,
    write_evaluation,
)
from stealth_prompt.core.strategies import StrategyStore, route_strategies

from .test_strategies import strategy


def benchmark_runs(
    benchmark: dict[str, object], strategy_id: str, library_snapshot: str
) -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    scenarios = benchmark["scenarios"]
    assert isinstance(scenarios, list)
    for scenario in scenarios:
        assert isinstance(scenario, dict)
        for variant in ("built_in", "learned"):
            if scenario["expected"] == "error":
                verdict = "error"
            elif scenario["safe_control"]:
                verdict = "not_observed"
            elif variant == "learned":
                verdict = "confirmed"
            else:
                verdict = "not_observed"
            selected = strategy_id if variant == "learned" else "builtin-boundary-probe"
            runs.append(
                {
                    "scenario_id": scenario["scenario_id"],
                    "variant": variant,
                    "library_snapshot_sha256": library_snapshot,
                    "verdict": verdict,
                    "deterministic": verdict == "confirmed",
                    "turns": 3,
                    "provider_calls": 4,
                    "input_tokens": 100,
                    "output_tokens": 40,
                    "latency_ms": 250,
                    "cost_usd": 0.01,
                    "selected_strategy_ids": [selected],
                    "confirmed_strategy_id": selected if verdict == "confirmed" else "",
                    "failure_signatures": (
                        ["explicit_refusal"] if not scenario["safe_control"] else []
                    ),
                    "library_mutations": 0,
                }
            )
    return runs


def frozen_report(store: StrategyStore) -> dict[str, Any]:
    benchmark = load_benchmark()
    snapshot = store.snapshot()
    private = snapshot["strategies"]
    assert isinstance(private, list) and private
    measurements = measurements_document(
        benchmark_id=str(benchmark["benchmark_id"]),
        snapshot_manifest_sha256=str(snapshot["manifest_sha256"]),
        runs=benchmark_runs(
            benchmark,
            str(private[0]["strategy_id"]),
            routing_snapshot_sha256(snapshot),
        ),
    )
    return evaluate_frozen(snapshot, measurements, benchmark=benchmark)


def test_frozen_evaluation_is_complete_measurement_backed_and_safe(tmp_path: Path) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    saved = store.save_revision(strategy(scope="target", scope_key="a" * 64))
    report = frozen_report(store)
    json_path, html_path = write_evaluation(tmp_path / "evaluation", report)

    assert report["quality_gate"]["passed"] is True
    assert report["completeness"]["missing_runs"] == []
    assert report["variants"]["learned"]["deterministic_asr"] == 1.0
    assert report["variants"]["built_in"]["deterministic_asr"] == 0.0
    assert saved["strategy_id"] in report["promotion_evidence"][
        "held_out_confirmed_strategy_ids"
    ]
    assert json_path.stat().st_mode & 0o777 == 0o600
    assert "stored measurements" in html_path.read_text()


def test_snapshot_provenance_matches_the_live_router(tmp_path: Path) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    store.save_revision(strategy(scope="target", scope_key="a" * 64))
    snapshot = store.snapshot()
    route = route_strategies(
        store,
        objective=Objective.INSTRUCTION_DISCLOSURE,
        mode="assist",
        state=AttackState(),
        target_scope_key="a" * 64,
    )

    assert routing_snapshot_sha256(snapshot) == route.library_snapshot_sha256


def test_safe_control_confirmation_fails_the_gate(tmp_path: Path) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    store.save_revision(strategy())
    benchmark = load_benchmark()
    snapshot = store.snapshot()
    runs = benchmark_runs(
        benchmark, "private-structural-probe", routing_snapshot_sha256(snapshot)
    )
    safe = next(run for run in runs if run["scenario_id"] == "approval-boundary-safe")
    safe.update(verdict="confirmed", deterministic=True)
    measurements = measurements_document(
        benchmark_id=str(benchmark["benchmark_id"]),
        snapshot_manifest_sha256=str(snapshot["manifest_sha256"]),
        runs=runs,
    )

    report = evaluate_frozen(snapshot, measurements, benchmark=benchmark)

    assert report["quality_gate"]["passed"] is False
    assert report["quality_gate"]["checks"]["safe_controls_zero_confirmed"] is False


def test_unexpected_execution_error_fails_the_gate(tmp_path: Path) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    store.save_revision(strategy())
    benchmark = load_benchmark()
    snapshot = store.snapshot()
    runs = benchmark_runs(
        benchmark, "private-structural-probe", routing_snapshot_sha256(snapshot)
    )
    failed = next(
        run
        for run in runs
        if run["scenario_id"] == "direct-disclosure" and run["variant"] == "learned"
    )
    failed.update(verdict="error", deterministic=False, confirmed_strategy_id="")
    measurements = measurements_document(
        benchmark_id=str(benchmark["benchmark_id"]),
        snapshot_manifest_sha256=str(snapshot["manifest_sha256"]),
        runs=runs,
    )

    report = evaluate_frozen(snapshot, measurements, benchmark=benchmark)

    assert report["quality_gate"]["passed"] is False
    assert report["quality_gate"]["checks"]["expected_execution_outcomes"] is False


def test_held_out_measurement_cannot_claim_a_library_mutation(tmp_path: Path) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    store.save_revision(strategy())
    benchmark = load_benchmark()
    snapshot = store.snapshot()
    runs = benchmark_runs(
        benchmark, "private-structural-probe", routing_snapshot_sha256(snapshot)
    )
    held_out = next(run for run in runs if run["scenario_id"] == "partial-verification")
    held_out["library_mutations"] = 1
    measurements = measurements_document(
        benchmark_id=str(benchmark["benchmark_id"]),
        snapshot_manifest_sha256=str(snapshot["manifest_sha256"]),
        runs=runs,
    )

    with pytest.raises(EvaluationError, match="held-out"):
        evaluate_frozen(snapshot, measurements, benchmark=benchmark)


def test_evaluation_rejects_duplicate_matrix_rows(tmp_path: Path) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    store.save_revision(strategy())
    benchmark = load_benchmark()
    snapshot = store.snapshot()
    runs = benchmark_runs(
        benchmark, "private-structural-probe", routing_snapshot_sha256(snapshot)
    )
    runs.append(dict(runs[0]))
    measurements = measurements_document(
        benchmark_id=str(benchmark["benchmark_id"]),
        snapshot_manifest_sha256=str(snapshot["manifest_sha256"]),
        runs=runs,
    )

    with pytest.raises(EvaluationError, match="duplicate"):
        evaluate_frozen(snapshot, measurements, benchmark=benchmark)


def _experience(index: int, strategy_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "experience_id": f"experience-confirmed-{index}",
        "report_id": f"assistant-20260814T12000{index}Z-abc12{index}",
        "turn_id": f"turn-{index}",
        "objective_id": "instruction_disclosure",
        "strategy_id": strategy_id,
        "outcome": "confirmed",
        "deterministic": True,
        "target_surface_tags": ["chat"],
        "capability_tags": [],
        "target_model_family": "",
        "failure_signature": "partial_disclosure",
        "pivot_reason": "A structural pivot exposed deterministic evidence.",
        "payload_template": "[PAYLOAD OMITTED]",
        "expected_signal_shape": ["verdict:confirmed"],
        "evidence_sha256": str(index) * 64,
        "provider": "fake",
        "latency_ms": 10,
        "turn_count": 2,
        "eligibility": "eligible",
        "review_status": "accepted",
        "created_at": f"2026-08-14T12:00:0{index}+00:00",
    }


def test_promotion_requires_measurement_gate_and_two_independent_confirmations(
    tmp_path: Path,
) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    saved = store.save_revision(strategy(scope="target", scope_key="b" * 64))
    report = frozen_report(store)

    blocked = promotion_preview(
        store, report, strategy_id=saved["strategy_id"], scope="private_global"
    )
    assert blocked["eligible"] is False

    for index in (1, 2):
        experience = _experience(index, saved["strategy_id"])
        store.apply_digest(
            report_id=str(experience["report_id"]),
            digest_action="reinforce",
            digest_sha256=str(index) * 64,
            strategy=None,
            experience=experience,
            decision="accept",
        )

    preview = promotion_preview(
        store, report, strategy_id=saved["strategy_id"], scope="private_global"
    )
    promoted = store.save_revision(
        preview["after"], source=preview["after"]["source"], action="promote"
    )

    assert preview["eligible"] is True
    assert store.aggregate_statistics(min_attempts=2) == [
        {
            "strategy_id": saved["strategy_id"],
            "objective_id": "instruction_disclosure",
            "attempts": 2,
            "confirmed": 2,
            "potential": 0,
            "not_observed": 0,
        }
    ]
    assert promoted["scope"] == "private_global"
    tampered = json.loads(json.dumps(report))
    tampered["quality_gate"]["passed"] = False
    with pytest.raises(EvaluationError, match="hash"):
        promotion_preview(
            store, tampered, strategy_id=saved["strategy_id"], scope="private_global"
        )


def test_report_measurement_copies_no_payload_response_or_origin() -> None:
    report = {
        "schema_version": 1,
        "kind": "assistant_session",
        "verdict": "potential",
        "configuration": {
            "origin": "https://tenant-secret.example",
            "use_private_strategies": False,
        },
        "turns": [
            {
                "approved_payload": "SP_CANARY_DO_NOT_COPY",
                "response": "Secret tenant reply",
                "proposal": {
                    "strategy_id": "builtin-boundary-probe",
                    "library_snapshot_sha256": "a" * 64,
                },
                "evaluation": {
                    "verdict": "potential",
                    "failure_signature": "partial_disclosure",
                },
            }
        ],
        "timeline": {"events": []},
    }

    measurement = measurement_from_report(
        report, scenario_id="partial-verification", variant="built_in"
    )
    rendered = json.dumps(measurement)

    assert "SP_CANARY" not in rendered
    assert "tenant-secret" not in rendered
    assert "Secret tenant reply" not in rendered
