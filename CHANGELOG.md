# Changelog

All notable changes are documented here. The project follows Semantic Versioning.

## [Unreleased]

## [0.3.0] - 2026-09-02

### Added

- FrameFuzz matched framing experiments for compatible indirect-injection and
  disclosure objectives, using one normalized semantic seed and a closed, versioned
  template pack across clean, explicit, integrity-signature, required-configuration,
  and trusted-destination cases.
- Per-case fresh-conversation gates, read-only binding revalidation, case-scoped Auto
  authorization, deterministic Core conclusions, potential-capped Direct conclusions,
  reproducible ordering, and matched-comparison views in JSON, HTML, and the extension.
- Scenario schema v3 with fail-closed FrameFuzz configuration, backward-compatible
  v1/v2 imports with the feature disabled, and framing-sensitive/safe local demo modes.
- Versioned `local-v1` train/held-out benchmark and frozen, report-backed evaluation
  comparing built-in and learned routing with snapshot provenance, deterministic ASR,
  transfer, routing, pivot, cost, latency, digest-quality, and safe-control metrics.
- Fixed promotion gates requiring the evaluated active revision, held-out deterministic
  attribution, zero safe-control confirmations, documented failure conditions, and two
  independently evidenced operator-accepted confirmations.
- File-only Ed25519-signed team and community strategy packs with publisher fingerprints,
  strict preview/trust gates, private-global-only community scope, and k-bounded aggregate
  effectiveness counts. Packs never contain reports or captured target evidence.
- Review-gated local learning for eligible Core reports. Reports can preview the exact
  sanitized digest input and proposed `reinforce`, `widen`, `create`, `limit`, or
  `no_change` action, then Accept, Edit, Keep target-specific, or Reject it.
- Atomic, idempotent digest application with immutable strategy revisions, bounded
  abstract experiences, audit-only rejection, and deterministic routing feedback.
- Learning privacy controls that omit raw responses, raw payloads, origins and identity
  values; credential-shaped evidence fails eligibility instead of entering a digest.
- Core-only learning controls in Settings and Reports, with a read-only Direct API
  boundary and an optional path back to Local Core.
- Core-owned private strategy library backed by SQLite with immutable revisions,
  active/disabled state, rollback, owner-only files, deterministic snapshots, and a
  preview-token-gated import flow. Imports reject unknown fields, credential-shaped
  values, browser operations and reserved built-in IDs before any transaction begins.
- A versioned read-only seed strategy catalogue, strategy-library capability frames,
  a configurable `--strategy-db` path, and a corresponding `doctor` check.
- Deterministic strategy routing with objective, scope, surface, mode, capability,
  response-pattern, and exhausted-move filters. The planner sees at most three bounded
  summaries from eight candidates, may rerank only the offered IDs, and falls back to
  the deterministic top choice. Direct API uses the same bounded flow with the shipped
  read-only catalogue.
- Auditable routing attribution in JSON and HTML reports: candidate IDs, selected
  strategy/move, router version, library snapshot hash, selection method, and the prior
  attempt that caused a pivot.
- Versioned whole-run attack state for Core and Direct API assessments. Planning now
  retains bounded structural outcomes across up to 100 turns while keeping only the
  latest two shared turns in full, instead of forgetting everything before turn three.
- Closed strategy, move, failure-signature and pivot attribution in proposals,
  in-product results, JSON exports and self-contained HTML reports. Provider-invented
  identifiers fail closed, and exact repeated replies are detected by hash without
  copying the target response into structural memory.
- Side Panel information architecture and visual system.
- Persistent external-provider and browser-held credential warnings without blocking consent checkboxes.
- Expanded AI-agent security objective catalogue.
- Self-contained HTML evidence report alongside JSON export.
- Product roadmap, privacy, security and contribution documentation.
- TypeScript quality and build checks in CI.
- Read-only chat element discovery with confidence and manual review.
- Human verification pause when Auto produces a potential finding.
- Configurable Auto finding policy: pause for review, stop and save on the first
  potential signal, or continue while recording every turn. Terminal Auto runs
  now save automatically and open Reports; turn budgets support up to 100 sends.
- Scenario schema v2 records the Auto finding policy; version 1 scenarios remain
  readable and migrate to the review policy.
- New small-size extension icon.
- Binding health (healthy / re-checking / needs review / unsupported) with
  read-only revalidation after reload, same-origin navigation, SPA document
  replacement, panel reopen, and immediately before every fill or submit.
  A failed check pauses Auto, revokes automatic-send authorization, names the
  failing role, and keeps the reviewed binding for recovery.
- Pre-mutation binding revalidation enforced at the service-worker chokepoint,
  so a stale or ambiguous locator fails closed before touching the page.
- Per-role discovery confidence and reasons, on-demand element highlighting, and
  independent accept/replace for each role.
- `stealth-prompt demo`: one command that starts the local demo target and the
  Core together with the demo canary pre-configured as a deterministic check,
  and prints recovery steps for permission, pairing and binding failures.
- Versioned scenario files (schema v1) with export, two-step import preview,
  explicit origin-mismatch warning, and a distinct version-mismatch error.
  Scenarios refuse credential- and capture-shaped fields, never restore
  automatic-send authorization, and always require fresh binding validation.
- Deterministic scorer set: fragment, regex, structured JSON field, DOM
  assertion, navigation/origin assertion and explicit human confirmation, each
  reporting scorer id, status, bounded evidence, SHA-256, deterministic flag,
  reason, timestamp and turn id.
- Scorer provenance in the HTML and JSON reports, including scorers that did not
  match and scorers that could not run, so `not_detected` is distinguishable
  from "never checked".
- Visible focus indicators, ARIA labelling and a live-region status for binding
  health, reduced-motion handling, and wrapping for long selectors and errors.
- Guided connection → AI → target → interaction → run navigation and a harmless
  draft-fill check that never presses Send.
- Session-only direct OpenAI Responses API and Anthropic Messages API connections,
  including live model discovery, optional host permission, cancellation, a visible
  credential-risk warning, and key-free JSON export.
- Browser-local Direct API report history backed by IndexedDB, with automatic
  snapshots, in-product viewing, JSON download, per-report deletion, bounded
  retention, and no persisted provider key.
- Dedicated Behavior, Evidence & reports, and Settings sections with report export
  and separated advanced run bounds.

- Session-centric Side Panel workspaces: Setup, Test, Review and Reports are
  separately rendered screens behind an ARIA `tablist`, with keyboard
  navigation, a modal Settings drawer, and no anchor navigation.
- Automatic workspace transitions driven by assessment state: start opens the
  live run, a potential finding pauses Auto and opens the review, confirming or
  stopping opens a terminal run summary, and a reload reopens the workspace the
  assessment is in.
- Durable run lifecycle (`sessionEnded`) separated from transient navigation
  state, so navigation is recomputed rather than persisted.
- Report library for Local Core: `reports.list` and `reports.open` frames
  serving bounded metadata derived from the existing artifact store, with
  report-id and artifact allowlists and a resolved-path check inside the
  artifacts root.

- Progressive Setup: the current step is expanded, finished steps collapse to a
  one-line summary, and the primary action stays reachable without scrolling at
  320 px.
- A restrained interface token system (elevation, text, border, spacing, radius,
  control height, focus and motion scales), replacing per-group cards with space and
  hairline rules.
- Run state is carried by a left accent rule and fixed-height status rows rather
  than by flooding cards with colour, so the card no longer resizes as a run
  moves between states.
- Finding Review now shows evidence before the decision controls, and continuing
  (the reversible choice) is the primary action rather than confirming.

### Fixed

- The verdict is stated once per screen. The session header no longer printed a
  verdict that could contradict the finding review directly beneath it.
- The connection pill kept a stale value once the Connection group collapsed; it
  is now refreshed by the top-level render.
- An error filed against a collapsed Setup step is no longer invisible: filing
  it reopens the owning step.
- Removed decorative page glow, the "Generated: 0.0s" timing, and the empty
  "Events" tile; internal event names in the timeline are now product language.
- No interface text below 11 px, no undersized hit targets, and the layout holds
  to 240 px (a 480 px panel at 200 % zoom) without horizontal scrolling.

- A pairing code typed into the Side Panel is no longer discarded when a
  background event re-renders the panel.
- Successful connection retries now clear stale error banners and failure details.
- Errors now render beside the connection, provider, target, interaction, test, or
  evidence controls that produced them, with an explicit dismiss action.

### Changed

- Extension-first installation and product narrative.
- Public documentation now focuses on installation, testing, reports, privacy, and a
  concise roadmap; obsolete development and migration material was removed.
- Core WebSocket dependency is part of the default package.
- Auto resumes the already prepared proposal after an unconfirmed review instead
  of paying for another generation.
- Local Core / Direct API selection now uses one accessible switch; starting Auto
  is itself the bounded-send authorization.

## [0.1.0]

- Initial browser extension, local Core, provider adapters, element binding,
  payload-only/assist/guided/auto modes, deterministic oracles and JSON evidence.
