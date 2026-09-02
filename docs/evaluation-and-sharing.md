# Evaluate and share strategies

Frozen evaluation and signed strategy packs are Core-only workflows for teams that
maintain a reviewed private strategy library. They are not required for an ordinary
browser assessment.

## Frozen evaluation

Evaluation asks whether a reviewed library improved measured results over the built-in
catalogue without making negative controls unsafe. It does not contact a provider or
target and never opens the mutable library. It reads one immutable strategy snapshot,
stored Core reports or versioned measurements, and the bundled `local-v1` benchmark.

### 1. Freeze the library

```bash
stealth-prompt library \
  --strategy-db .stealth-prompt/strategies.sqlite3 \
  snapshot-export --output strategy-snapshots
```

The owner-only JSON snapshot contains the built-in catalogue identity and active
reviewed strategies—not reports, responses, credentials, or API keys. Do not modify the
library between comparison runs. Every non-error report must carry the routing hash
derived from this exact snapshot; mixed or missing provenance is rejected.

### 2. Collect the fixed matrix

Run each benchmark scenario twice against the same controlled target behavior:

| Variant | Core setting |
| --- | --- |
| `built_in` | **Settings → Local learning → Use accepted private strategies** off |
| `learned` | The same setting on, using the frozen private library |

`local-v1` pairs positive and safe-control behavior for direct disclosure, multi-turn
continuation, refusal pivots, partial disclosure verification, capability mapping, and
approval boundaries. Capture and provider failures are separate held-out controls. Its
fixed training and held-out definitions live in
`src/stealth_prompt/core/benchmarks/local-v1.json`.

Use synthetic secrets and targets you own. Held-out reports must never be offered for
learning or mutate the library.

### 3. Build the comparison

Pass a stored `session.json` for every scenario/variant pair. `--run` is repeatable:

```bash
stealth-prompt evaluate \
  --snapshot strategy-snapshots/strategy-library-<hash>.json \
  --run direct-disclosure built_in results/baseline-direct/session.json \
  --run direct-disclosure learned results/learned-direct/session.json \
  --run direct-disclosure-safe built_in results/baseline-safe/session.json \
  --run direct-disclosure-safe learned results/learned-safe/session.json \
  --output evaluation-results
```

Continue with all pairs in the bundled benchmark. A release harness may instead pass
`--measurements measurements.json`; its schema is strict and versioned. Duplicate rows,
unknown fields, mixed snapshots, confirmations without deterministic evidence, and
held-out library mutations fail closed.

The command writes owner-only JSON and script-free HTML. Exit status is `0` only when
the matrix is complete, safe controls have zero deterministic confirmations, no library
secret leak was recorded, and held-out runs made no library mutation. A failed quality
gate returns `5`; invalid input returns `1`.

Metrics include deterministic attack success rate with a 95% interval when the sample
is large enough, potential rate, median turns, provider calls, tokens, latency, reported
cost, held-out transfer, top-1/top-3 routing, pivot success, digest quality, and safe
controls. Unknown cost or token data remains `null`; Stealth Prompt does not estimate it.

## Promote a strategy

Promotion broadens scope; it does not create a new strategy. Preview it first:

```bash
stealth-prompt library \
  --strategy-db .stealth-prompt/strategies.sqlite3 \
  promote \
  --strategy-id private-example \
  --evaluation evaluation-results/evaluation-<hash>.json \
  --scope private_global
```

The fixed gate requires the exact active revision from the frozen snapshot, a passed
evaluation, held-out deterministic attribution, zero safe-control confirmations,
documented failure conditions, and two independently evidenced, operator-accepted
confirmations. Preview never changes the library.

Apply the same eligible preview explicitly:

```bash
stealth-prompt library \
  --strategy-db .stealth-prompt/strategies.sqlite3 \
  promote \
  --strategy-id private-example \
  --evaluation evaluation-results/evaluation-<hash>.json \
  --scope private_global \
  --apply --yes
```

Project promotion also requires the intended SHA-256 project scope key. Built-ins are
immutable, and promotion can only move a strategy to a broader scope.

## Signed strategy packs

A pack is a file for deliberate team exchange or code review. It contains validated
strategy revisions, a signed publisher manifest, policy, and aggregate counts only
after the configured minimum number of attempts. It never contains reports, payloads,
target responses, report/turn IDs, credentials, origins, or evidence records.

Generate an Ed25519 publisher key and keep it outside the repository:

```bash
openssl genpkey -algorithm Ed25519 -out publisher-private.pem
chmod 600 publisher-private.pem
```

Export a team pack:

```bash
stealth-prompt library \
  --strategy-db .stealth-prompt/strategies.sqlite3 \
  pack-export \
  --publisher "Example security team" \
  --private-key publisher-private.pem \
  --audience team \
  --minimum-attempts 5 \
  --output strategy-pack.json
```

`team` packs may carry target, project, and private-global reviewed strategies.
`community` packs accept private-global strategies only, so target-specific routing
cannot escape its scope. A community pull request should contain only the pack and
normal review context—never assessment reports.

The default aggregate threshold is five attempts. Raising it reduces disclosure risk;
lowering it to the supported minimum of two should be an explicit team decision.

Verify integrity and the expected publisher fingerprint:

```bash
stealth-prompt library pack-verify \
  --pack strategy-pack.json \
  --trusted-key-id <publisher-key-sha256>
```

The embedded public key proves signature integrity, not the publisher's real-world
identity. Confirm its SHA-256 fingerprint through a separate trusted channel.

Import is always a preview first:

```bash
stealth-prompt library \
  --strategy-db .stealth-prompt/strategies.sqlite3 \
  pack-import --pack strategy-pack.json
```

After reviewing every create/revise action, apply with the exact fingerprint:

```bash
stealth-prompt library \
  --strategy-db .stealth-prompt/strategies.sqlite3 \
  pack-import \
  --pack strategy-pack.json \
  --trusted-key-id <publisher-key-sha256> \
  --apply --yes
```

Schema and signature validation, secret/operation rejection, preview, and explicit
trust all run before a transaction changes the destination library.

## Local-first boundary

Stealth Prompt has no cloud ingestion path for reports, strategies, or effectiveness
statistics. Sharing is an explicit file operation. Aggregate counts are k-bounded and
carry no report, target, provider, or turn identifiers. Deleting or never exporting a
pack keeps the library private to that Core installation.
