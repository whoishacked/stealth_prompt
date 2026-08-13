"""Characterization tests for the legacy successful-prompt database.

All fixture values are synthetic. No real credential or captured target
response appears in this file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.prompt_db import PromptDB

# Synthetic canary used only by this test module.
CANARY = "SPCANARY7GH3KD"


def write_db(path: Path, entries: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def chain(*payload_response: tuple[str, str]) -> list[dict[str, Any]]:
    return [
        {"turn": i, "payload": payload, "response": response}
        for i, (payload, response) in enumerate(payload_response, start=1)
    ]


# Conversations whose opening payload does and does not match the saved chain.
MATCHING_PREFIX: list[dict[str, Any]] = [{"turn": 1, "payload": "first", "response": "refusal"}]
OTHER_PREFIX: list[dict[str, Any]] = [{"turn": 1, "payload": "different", "response": "refusal"}]


class TestLoadAndMigration:
    def test_absent_file_starts_empty(self, tmp_path: Path) -> None:
        db = PromptDB(str(tmp_path / "absent.json"))

        assert db.prompts == []

    def test_old_prompt_response_entry_is_migrated_to_a_chain(self, tmp_path: Path) -> None:
        path = write_db(
            tmp_path / "db.json",
            [{"test_type": "system_prompt_leakage", "prompt": "ask", "response": "reply"}],
        )

        db = PromptDB(str(path))

        entry = db.prompts[0]
        assert entry["conversation_chain"] == [{"turn": 1, "payload": "ask", "response": "reply"}]
        assert "prompt" not in entry
        assert "response" not in entry
        assert entry["id"]

    def test_legacy_chain_id_field_is_dropped(self, tmp_path: Path) -> None:
        path = write_db(
            tmp_path / "db.json",
            [
                {
                    "chain_id": "stale-identifier",
                    "test_type": "system_prompt_leakage",
                    "conversation_chain": chain(("ask", "reply")),
                }
            ],
        )

        db = PromptDB(str(path))

        assert "chain_id" not in db.prompts[0]
        assert db.prompts[0]["id"]

    def test_migration_is_written_back_to_disk(self, tmp_path: Path) -> None:
        path = write_db(
            tmp_path / "db.json",
            [{"test_type": "system_prompt_leakage", "prompt": "ask", "response": "reply"}],
        )

        PromptDB(str(path))

        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert "conversation_chain" in on_disk[0]
        assert "prompt" not in on_disk[0]

    def test_already_migrated_database_is_not_rewritten(self, tmp_path: Path) -> None:
        entries = [
            {
                "id": "abc123",
                "test_type": "system_prompt_leakage",
                "conversation_chain": chain(("ask", "reply")),
                "confirmed_by_user": True,
            }
        ]
        path = write_db(tmp_path / "db.json", entries)
        before = path.read_text(encoding="utf-8")

        PromptDB(str(path))

        assert path.read_text(encoding="utf-8") == before

    def test_corrupt_database_is_reported_and_treated_as_empty(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "db.json"
        path.write_text("{not valid json", encoding="utf-8")

        db = PromptDB(str(path))

        assert db.prompts == []
        assert "Error loading database" in capsys.readouterr().out


class TestAddPrompt:
    def test_single_prompt_becomes_a_one_turn_chain(self, tmp_path: Path) -> None:
        db = PromptDB(str(tmp_path / "db.json"))

        db.add_prompt("ask", "system_prompt_leakage", "reply")

        assert db.prompts[0]["conversation_chain"] == [
            {"turn": 1, "payload": "ask", "response": "reply"}
        ]
        assert db.prompts[0]["confirmed_by_user"] is True
        assert db.prompts[0]["added_at"]

    def test_explicit_chain_is_stored_verbatim(self, tmp_path: Path) -> None:
        db = PromptDB(str(tmp_path / "db.json"))
        conversation = chain(("first", "refusal"), ("second", f"the value is {CANARY}"))

        db.add_prompt("second", "system_prompt_leakage", f"the value is {CANARY}",
                      conversation_chain=conversation)

        assert db.prompts[0]["conversation_chain"] == conversation

    def test_identical_chain_is_not_stored_twice(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        db = PromptDB(str(tmp_path / "db.json"))
        db.add_prompt("ask", "system_prompt_leakage", "reply")

        db.add_prompt("ask", "system_prompt_leakage", "reply")

        assert len(db.prompts) == 1
        assert "already exists" in capsys.readouterr().out

    def test_entry_survives_a_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "db.json"
        PromptDB(str(path)).add_prompt("ask", "system_prompt_leakage", "reply")

        reloaded = PromptDB(str(path))

        assert len(reloaded.prompts) == 1
        assert reloaded.prompts[0]["test_type"] == "system_prompt_leakage"


class TestQueries:
    @pytest.fixture
    def db(self, tmp_path: Path) -> PromptDB:
        database = PromptDB(str(tmp_path / "db.json"))
        database.add_prompt("ask-a", "system_prompt_leakage", "reply-a")
        database.add_prompt("ask-b", "data_extraction", "reply-b")
        return database

    def test_filters_by_test_type(self, db: PromptDB) -> None:
        assert len(db.get_successful_prompts("system_prompt_leakage")) == 1
        assert len(db.get_successful_prompts("data_extraction")) == 1
        assert len(db.get_successful_prompts("unauthorized_access")) == 0

    def test_returns_everything_without_a_filter(self, db: PromptDB) -> None:
        assert len(db.get_successful_prompts()) == 2
        assert len(db.get_all_prompts()) == 2

    def test_check_prompt_hashes_a_single_prompt_and_misses_chain_ids(self, db: PromptDB) -> None:
        # Entry IDs hash the whole chain, so a single-prompt hash lookup never matches.
        assert db.check_prompt("ask-a") is None

    def test_get_chain_by_id_finds_a_stored_entry(self, db: PromptDB) -> None:
        stored_id = db.prompts[0]["id"]

        assert db.get_chain_by_id(stored_id) is db.prompts[0]
        assert db.get_chain_by_id("no-such-id") is None


class TestSavedChainReplay:
    @pytest.fixture
    def db(self, tmp_path: Path) -> PromptDB:
        database = PromptDB(str(tmp_path / "db.json"))
        database.add_prompt(
            "second",
            "system_prompt_leakage",
            f"the value is {CANARY}",
            conversation_chain=chain(("first", "refusal"), ("second", f"the value is {CANARY}")),
        )
        return database

    def test_returns_next_payload_when_the_prefix_matches(self, db: PromptDB) -> None:
        next_prompt = db.try_saved_chain("system_prompt_leakage", MATCHING_PREFIX)

        assert next_prompt == "second"

    def test_returns_none_when_the_prefix_differs(self, db: PromptDB) -> None:
        next_prompt = db.try_saved_chain("system_prompt_leakage", OTHER_PREFIX)

        assert next_prompt is None

    def test_returns_none_when_the_chain_is_exhausted(self, db: PromptDB) -> None:
        current = chain(("first", "refusal"), ("second", "reply"))

        assert db.try_saved_chain("system_prompt_leakage", current) is None

    def test_returns_none_for_a_different_test_type(self, db: PromptDB) -> None:
        assert db.try_saved_chain("data_extraction", MATCHING_PREFIX) is None

    def test_log_reports_the_real_entry_id(
        self, db: PromptDB, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Regression test: the log previously read the removed 'chain_id' field
        # and always printed a truncated "unknown".
        db.try_saved_chain("system_prompt_leakage", MATCHING_PREFIX)

        out = capsys.readouterr().out
        assert f"Using saved chain (ID: {db.prompts[0]['id'][:8]}...)" in out
        assert "unknow" not in out


class TestResponseMatching:
    def test_matches_a_synthetic_value_seen_in_a_saved_response(self, tmp_path: Path) -> None:
        db = PromptDB(str(tmp_path / "db.json"))
        db.add_prompt("ask", "system_prompt_leakage", f"The password is {CANARY}")

        assert db.check_response_with_prompts(f"Sure, it is {CANARY}.", "system_prompt_leakage")

    def test_does_not_match_an_unrelated_refusal(self, tmp_path: Path) -> None:
        db = PromptDB(str(tmp_path / "db.json"))
        db.add_prompt("ask", "system_prompt_leakage", f"The password is {CANARY}")

        assert not db.check_response_with_prompts(
            "I'm sorry, but I cannot provide the password.", "system_prompt_leakage"
        )

    def test_returns_false_without_saved_entries_for_the_type(self, tmp_path: Path) -> None:
        db = PromptDB(str(tmp_path / "db.json"))

        assert not db.check_response_with_prompts(f"contains {CANARY}", "system_prompt_leakage")

    def test_does_not_match_across_test_types(self, tmp_path: Path) -> None:
        db = PromptDB(str(tmp_path / "db.json"))
        db.add_prompt("ask", "data_extraction", f"The password is {CANARY}")

        assert not db.check_response_with_prompts(
            f"contains {CANARY}", "system_prompt_leakage"
        )
