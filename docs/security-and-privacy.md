# Security and privacy

Stealth Prompt is local-first and has no required product account or hosted service that
receives target data.

## Trust boundaries

- The target page and every captured response are treated as hostile.
- Only the extension content executor touches the page, through a closed operation list.
- Local Core binds to loopback and requires an origin-bound pairing token.
- Core-mode executable paths and credentials never come from the target page or a model.
- Direct API keys are session-only and restricted to fixed provider API origins.
- A model verdict alone cannot create a confirmed finding.

## Chrome permissions

The extension requests `sidePanel`, `storage`, `scripting`, and `activeTab`. Host access
is optional and requested for the exact target or provider origin selected at runtime.
It does not request `cookies`, `webRequest`, `debugger`, or blanket site access at install.

## Data on this computer

Chrome local storage can contain configuration, reviewed locators, session identifiers,
and timeline metadata. Direct API reports live in extension-owned IndexedDB. Core evidence
lives in the configured artifact directory. Reports can contain sensitive target output.
Core's strategy library is a separate owner-only SQLite file. It stores validated,
revisioned abstractions and evidence references, never copied target replies or provider
credentials. Target/project routing uses SHA-256 scope identifiers; the library does not
need to retain a target URL to keep a scoped strategy from affecting unrelated targets.

Reviewed learning is opt-in per run and available only to Core reports. Eligibility is
checked from sent/captured evidence and binding health. The digest input omits the raw
response, raw payload, origin, cookies, storage and identity values; credential-shaped
content makes the report ineligible. The exact sanitized object and proposed before/after
change are shown before any mutation. Potential findings may add attempt evidence but
never count as success. Accept/Edit/Keep target-specific writes one atomic revision and
audit record; Reject writes an audit record only. Direct API reports are never copied
into Core automatically.

Frozen evaluation reads an immutable snapshot and stored bounded measurements without
opening the live library or contacting a provider or target. Session provenance must
match the routing hash derived from that snapshot. Promotion requires a passed
safe-control gate plus independent, operator-accepted deterministic evidence.

Optional strategy sharing is file-only. Packs contain reviewed strategies and
k-bounded aggregate counts, never reports or captured evidence, and are signed with
Ed25519 through local OpenSSL. Import requires schema/signature validation, preview,
and the exact trusted publisher fingerprint. Stealth Prompt does not operate a cloud
ingestion service.

The extension does not intentionally read cookies, passwords, target access tokens,
`localStorage`, or `sessionStorage`.

FrameFuzz template generation is local and deterministic. Its protected-value field is
a label, not a value; credential-shaped labels are rejected. The trusted destination
is inert payload data and is never fetched by Stealth Prompt. Executable schemes, URL
credentials, credential query fields, fragments, and control characters are refused.
Each case reuses the existing page-operation allowlist and requires a read-only binding
validation; FrameFuzz adds no navigation, click, reset, cookie, or network-observation
capability.

## Canonical policies

- [Privacy policy](https://github.com/whoishacked/stealth_prompt/blob/main/PRIVACY.md)
- [Security policy and private reporting](https://github.com/whoishacked/stealth_prompt/blob/main/SECURITY.md)
- [Detailed extension threat model](extension.md#threat-model)

Use Stealth Prompt only on systems you own or are explicitly authorized to assess.
