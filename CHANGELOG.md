# Changelog

All notable changes are documented here. The project follows Semantic Versioning.

## [Unreleased]

### Added

- Commercial-grade Side Panel information architecture and visual system.
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
  control height, focus and motion scales) documented in
  `docs/design-system.md`, replacing per-group cards with space and hairline
  rules.
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
- Core WebSocket dependency is part of the default package.
- Auto resumes the already prepared proposal after an unconfirmed review instead
  of paying for another generation.
- Local Core / Direct API selection now uses one accessible switch; starting Auto
  is itself the bounded-send authorization.

## [0.1.0]

- Initial browser extension, local Core, provider adapters, element binding,
  payload-only/assist/guided/auto modes, deterministic oracles and JSON evidence.
