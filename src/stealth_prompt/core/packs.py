"""Signed, report-free strategy packs for teams and community review.

Packs contain only validated strategy snapshots and k-bounded aggregate counts.
Ed25519 signing is delegated to the installed OpenSSL executable; no remote service or
new Python cryptography dependency is needed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import Objective
from .strategies import SCOPES, StrategyError, StrategyStore, parse_snapshot

PACK_SCHEMA_VERSION = 1
PACK_KIND = "stealth_prompt_strategy_pack"
PACK_ALGORITHM = "ed25519"
MAX_PACK_BYTES = 1024 * 1024
_ID = re.compile(r"^[a-z][a-z0-9_-]{2,79}$")


class PackError(ValueError):
    """A strategy pack, signing key, or trust decision was invalid."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _openssl() -> str:
    executable = shutil.which("openssl")
    if not executable:
        raise PackError("OpenSSL is required for Ed25519 strategy-pack signing")
    return executable


def _private_key(path: Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > 64 * 1024:
        raise PackError("the signing key must be a bounded regular file, not a symlink")
    if stat.S_IMODE(candidate.stat().st_mode) & 0o077:
        raise PackError("the signing key must not be readable by group or other users")
    return candidate


def _run_openssl(arguments: list[str], *, data: bytes = b"") -> bytes:
    try:
        completed = subprocess.run(
            [_openssl(), *arguments],
            input=data,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PackError(f"OpenSSL failed: {type(exc).__name__}") from None
    if completed.returncode != 0:
        raise PackError("OpenSSL rejected the Ed25519 key or signature")
    return completed.stdout


def _public_der(private_key: Path) -> bytes:
    return _run_openssl(["pkey", "-in", str(private_key), "-pubout", "-outform", "DER"])


def _sign(private_key: Path, data: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="stealth-prompt-sign-") as temporary:
        message_path = Path(temporary) / "manifest.json"
        message_path.write_bytes(data)
        return _run_openssl(
            [
                "pkeyutl",
                "-sign",
                "-rawin",
                "-inkey",
                str(private_key),
                "-in",
                str(message_path),
            ]
        )


def _verify(public_der: bytes, signature: bytes, data: bytes) -> None:
    with tempfile.TemporaryDirectory(prefix="stealth-prompt-pack-") as temporary:
        root = Path(temporary)
        public_path = root / "publisher.der"
        signature_path = root / "manifest.sig"
        message_path = root / "manifest.json"
        public_path.write_bytes(public_der)
        signature_path.write_bytes(signature)
        message_path.write_bytes(data)
        try:
            _run_openssl(
                [
                    "pkeyutl",
                    "-verify",
                    "-rawin",
                    "-pubin",
                    "-keyform",
                    "DER",
                    "-inkey",
                    str(public_path),
                    "-sigfile",
                    str(signature_path),
                    "-in",
                    str(message_path),
                ],
            )
        except PackError:
            raise PackError("strategy-pack signature verification failed") from None


def create_pack(
    store: StrategyStore,
    *,
    publisher: str,
    private_key: Path,
    audience: str = "team",
    min_aggregate_attempts: int = 5,
) -> dict[str, Any]:
    name = publisher.strip()
    if not 1 <= len(name) <= 120 or any(ord(character) < 32 for character in name):
        raise PackError("publisher must be a printable name of at most 120 characters")
    if audience not in {"team", "community"}:
        raise PackError("pack audience must be 'team' or 'community'")
    key_path = _private_key(private_key)
    public_der = _public_der(key_path)
    key_id = hashlib.sha256(public_der).hexdigest()
    allowed_scopes = (
        frozenset({"private_global"})
        if audience == "community"
        else frozenset({"target", "project", "private_global"})
    )
    snapshot = store.snapshot(scopes=allowed_scopes)
    if not snapshot["strategies"]:
        raise PackError(f"the active library has no {audience}-eligible private strategies")
    strategy_ids = {item["strategy_id"] for item in snapshot["strategies"]}
    aggregates = [
        item
        for item in store.aggregate_statistics(min_attempts=min_aggregate_attempts)
        if item["strategy_id"] in strategy_ids
    ]
    unsigned: dict[str, Any] = {
        "schema_version": PACK_SCHEMA_VERSION,
        "kind": PACK_KIND,
        "publisher": {"name": name, "key_id": key_id},
        "audience": audience,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snapshot": snapshot,
        "aggregates": aggregates,
        "policy": {
            "allowed_import_scopes": sorted(allowed_scopes),
            "minimum_aggregate_attempts": min_aggregate_attempts,
            "requires_import_preview": True,
            "requires_explicit_trust": True,
        },
    }
    signed = {**unsigned, "manifest_sha256": _sha256(unsigned)}
    signed["signature"] = {
        "algorithm": PACK_ALGORITHM,
        "key_id": key_id,
        "public_key_der": base64.b64encode(public_der).decode("ascii"),
        "value": base64.b64encode(_sign(key_path, _canonical(signed))).decode("ascii"),
    }
    if len(_canonical(signed)) > MAX_PACK_BYTES:
        raise PackError("strategy pack is too large")
    return signed


def _parse_aggregates(value: object, strategy_ids: set[str], minimum: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 10_000:
        raise PackError("pack aggregates must be a bounded list")
    fields = {
        "strategy_id",
        "objective_id",
        "attempts",
        "confirmed",
        "potential",
        "not_observed",
    }
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict) or set(raw) != fields:
            raise PackError(f"aggregate {index + 1} has missing or unknown fields")
        strategy_id = raw.get("strategy_id")
        objective_id = raw.get("objective_id")
        if (
            strategy_id not in strategy_ids
            or not isinstance(objective_id, str)
            or objective_id not in {objective.value for objective in Objective}
        ):
            raise PackError(f"aggregate {index + 1} has an invalid attribution")
        counts: dict[str, int] = {}
        for key in fields - {"strategy_id", "objective_id"}:
            count = raw.get(key)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise PackError(f"aggregate {index + 1} has an invalid count")
            counts[key] = count
        attempts = counts["attempts"]
        if (
            attempts < minimum
            or sum(counts[key] for key in ("confirmed", "potential", "not_observed"))
            > attempts
        ):
            raise PackError(f"aggregate {index + 1} violates the minimum or totals")
        normalized.append(dict(raw))
    return normalized


def parse_pack(value: object, *, verify_signature: bool = True) -> dict[str, Any]:
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_PACK_BYTES:
            raise PackError("strategy pack is too large")
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PackError(f"strategy pack is not valid JSON: {exc.msg}") from None
    fields = {
        "schema_version",
        "kind",
        "publisher",
        "audience",
        "created_at",
        "snapshot",
        "aggregates",
        "policy",
        "manifest_sha256",
        "signature",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or len(_canonical(value)) > MAX_PACK_BYTES
    ):
        raise PackError("strategy pack has missing, unknown, or oversized fields")
    if value.get("schema_version") != PACK_SCHEMA_VERSION or value.get("kind") != PACK_KIND:
        raise PackError("unsupported strategy pack format")
    audience = value.get("audience")
    if audience not in {"team", "community"}:
        raise PackError("strategy pack has an invalid audience")
    publisher = value.get("publisher")
    if not isinstance(publisher, dict) or set(publisher) != {"name", "key_id"}:
        raise PackError("strategy pack publisher is invalid")
    name = publisher.get("name")
    key_id = publisher.get("key_id")
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 120
        or not isinstance(key_id, str)
        or not re.fullmatch(r"[a-f0-9]{64}", key_id)
    ):
        raise PackError("strategy pack publisher identity is invalid")
    created_at = value.get("created_at")
    if not isinstance(created_at, str) or not created_at or len(created_at) > 64:
        raise PackError("strategy pack created_at is invalid")
    try:
        snapshot = parse_snapshot(value.get("snapshot"))
    except StrategyError as exc:
        raise PackError(str(exc)) from None
    policy = value.get("policy")
    policy_fields = {
        "allowed_import_scopes",
        "minimum_aggregate_attempts",
        "requires_import_preview",
        "requires_explicit_trust",
    }
    if not isinstance(policy, dict) or set(policy) != policy_fields:
        raise PackError("strategy pack policy is invalid")
    scopes = policy.get("allowed_import_scopes")
    if (
        not isinstance(scopes, list)
        or not scopes
        or any(scope not in SCOPES - {"built_in"} for scope in scopes)
    ):
        raise PackError("strategy pack import scopes are invalid")
    if audience == "community" and set(scopes) != {"private_global"}:
        raise PackError("community packs may contain only private-global strategies")
    if any(item["scope"] not in scopes for item in snapshot["strategies"]):
        raise PackError("strategy pack contains a scope forbidden by its policy")
    minimum = policy.get("minimum_aggregate_attempts")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or not 2 <= minimum <= 100:
        raise PackError("strategy pack aggregate minimum is invalid")
    if (
        policy.get("requires_import_preview") is not True
        or policy.get("requires_explicit_trust") is not True
    ):
        raise PackError("strategy pack cannot weaken preview or trust requirements")
    strategy_ids = {item["strategy_id"] for item in snapshot["strategies"]}
    aggregates = _parse_aggregates(value.get("aggregates"), strategy_ids, minimum)
    unsigned = {
        key: value[key]
        for key in (
            "schema_version",
            "kind",
            "publisher",
            "audience",
            "created_at",
            "snapshot",
            "aggregates",
            "policy",
        )
    }
    if value.get("manifest_sha256") != _sha256(unsigned):
        raise PackError("strategy pack manifest hash does not match")
    signature = value.get("signature")
    if not isinstance(signature, dict) or set(signature) != {
        "algorithm",
        "key_id",
        "public_key_der",
        "value",
    }:
        raise PackError("strategy pack signature is invalid")
    if signature.get("algorithm") != PACK_ALGORITHM or signature.get("key_id") != key_id:
        raise PackError("strategy pack signature identity is invalid")
    try:
        public_der = base64.b64decode(str(signature.get("public_key_der")), validate=True)
        signature_bytes = base64.b64decode(str(signature.get("value")), validate=True)
    except (ValueError, TypeError):
        raise PackError("strategy pack signature is not valid base64") from None
    if (
        len(public_der) > 1024
        or len(signature_bytes) > 1024
        or hashlib.sha256(public_der).hexdigest() != key_id
    ):
        raise PackError("strategy pack public key does not match its fingerprint")
    signed = {**unsigned, "manifest_sha256": value["manifest_sha256"]}
    if verify_signature:
        _verify(public_der, signature_bytes, _canonical(signed))
    return {
        **signed,
        "snapshot": snapshot,
        "aggregates": aggregates,
        "signature": dict(signature),
    }


def read_pack(path: Path) -> dict[str, Any]:
    candidate = Path(path).expanduser()
    if (
        candidate.is_symlink()
        or not candidate.is_file()
        or candidate.stat().st_size > MAX_PACK_BYTES
    ):
        raise PackError("strategy pack must be a bounded regular file, not a symlink")
    try:
        return parse_pack(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise PackError(f"could not read strategy pack: {type(exc).__name__}") from None


def write_pack(path: Path, pack: dict[str, Any]) -> Path:
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, destination)
    return destination
