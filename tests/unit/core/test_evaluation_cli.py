from __future__ import annotations

import io
import json
from pathlib import Path

from stealth_prompt.cli import ExitCode, build_parser, main
from stealth_prompt.core.evaluation import (
    load_benchmark,
    measurements_document,
    routing_snapshot_sha256,
)
from stealth_prompt.core.packs import read_pack
from stealth_prompt.core.strategies import StrategyStore

from .test_evaluation import _experience, benchmark_runs, frozen_report
from .test_packs import signing_key
from .test_strategies import strategy


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_evaluate_cli_writes_frozen_json_and_html(tmp_path: Path) -> None:
    store = StrategyStore(tmp_path / "strategies.sqlite3")
    saved = store.save_revision(strategy(scope="target", scope_key="d" * 64))
    snapshot = store.snapshot()
    benchmark = load_benchmark()
    measurements = measurements_document(
        benchmark_id=str(benchmark["benchmark_id"]),
        snapshot_manifest_sha256=str(snapshot["manifest_sha256"]),
        runs=benchmark_runs(
            benchmark, saved["strategy_id"], routing_snapshot_sha256(snapshot)
        ),
    )
    snapshot_out, snapshot_err = io.StringIO(), io.StringIO()
    snapshot_code = main(
        [
            "library",
            "--strategy-db",
            str(tmp_path / "strategies.sqlite3"),
            "snapshot-export",
            "--output",
            str(tmp_path / "snapshots"),
        ],
        stdout=snapshot_out,
        stderr=snapshot_err,
    )
    snapshot_path = next((tmp_path / "snapshots").glob("strategy-library-*.json"))
    measurement_path = write_json(tmp_path / "measurements.json", measurements)
    out, err = io.StringIO(), io.StringIO()

    code = main(
        [
            "evaluate",
            "--snapshot",
            str(snapshot_path),
            "--measurements",
            str(measurement_path),
            "--output",
            str(tmp_path / "evaluation"),
        ],
        stdout=out,
        stderr=err,
    )

    assert snapshot_code == int(ExitCode.OK)
    assert snapshot_err.getvalue() == ""
    assert code == int(ExitCode.OK)
    assert "Quality gate: passed" in out.getvalue()
    assert err.getvalue() == ""
    assert len(list((tmp_path / "evaluation").glob("evaluation-*.json"))) == 1
    assert len(list((tmp_path / "evaluation").glob("evaluation-*.html"))) == 1


def test_pack_cli_requires_exact_signer_trust_before_apply(tmp_path: Path) -> None:
    source_path = tmp_path / "source.sqlite3"
    source = StrategyStore(source_path)
    source.save_revision(strategy(scope="private_global"))
    pack_path = tmp_path / "community-pack.json"
    key = signing_key(tmp_path)
    out, err = io.StringIO(), io.StringIO()

    exported = main(
        [
            "library",
            "--strategy-db",
            str(source_path),
            "pack-export",
            "--publisher",
            "Test publisher",
            "--private-key",
            str(key),
            "--audience",
            "community",
            "--output",
            str(pack_path),
        ],
        stdout=out,
        stderr=err,
    )
    pack = read_pack(pack_path)
    key_id = pack["publisher"]["key_id"]
    destination_path = tmp_path / "destination.sqlite3"

    verified = main(
        [
            "library",
            "pack-verify",
            "--pack",
            str(pack_path),
            "--trusted-key-id",
            key_id,
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    previewed = main(
        [
            "library",
            "--strategy-db",
            str(destination_path),
            "pack-import",
            "--pack",
            str(pack_path),
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    refused = main(
        [
            "library",
            "--strategy-db",
            str(destination_path),
            "pack-import",
            "--pack",
            str(pack_path),
            "--apply",
            "--yes",
            "--trusted-key-id",
            "0" * 64,
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    applied = main(
        [
            "library",
            "--strategy-db",
            str(destination_path),
            "pack-import",
            "--pack",
            str(pack_path),
            "--apply",
            "--yes",
            "--trusted-key-id",
            key_id,
        ],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert exported == int(ExitCode.OK)
    assert verified == int(ExitCode.OK)
    assert previewed == int(ExitCode.OK)
    assert refused == int(ExitCode.CONFIG_ERROR)
    assert applied == int(ExitCode.OK)
    assert StrategyStore(destination_path).capabilities()["private_count"] == 1


def test_promotion_cli_previews_then_applies_an_eligible_revision(tmp_path: Path) -> None:
    database = tmp_path / "strategies.sqlite3"
    store = StrategyStore(database)
    saved = store.save_revision(strategy(scope="target", scope_key="d" * 64))
    evaluation = frozen_report(store)
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
    evaluation_path = write_json(tmp_path / "evaluation.json", evaluation)
    command = [
        "library",
        "--strategy-db",
        str(database),
        "promote",
        "--strategy-id",
        saved["strategy_id"],
        "--evaluation",
        str(evaluation_path),
        "--scope",
        "private_global",
    ]

    preview = main(command, stdout=io.StringIO(), stderr=io.StringIO())
    applied = main(
        [*command, "--apply", "--yes"], stdout=io.StringIO(), stderr=io.StringIO()
    )

    assert preview == int(ExitCode.OK)
    assert applied == int(ExitCode.OK)
    assert StrategyStore(database).open_strategy(saved["strategy_id"])["scope"] == "private_global"


def test_new_commands_are_visible_in_help() -> None:
    help_text = build_parser().format_help()

    assert "evaluate" in help_text
    assert "library" in help_text
