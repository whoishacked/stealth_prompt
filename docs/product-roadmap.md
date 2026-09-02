# Product roadmap

This roadmap describes direction, not release commitments. Stealth Prompt remains
focused on authorized testing of selected AI interactions in a real browser session.

## Current priorities

- validate and extend the shipped FrameFuzz template pack with reproducible,
  authorization-safe fixtures and published compatibility results;
- improve element detection and response capture across common chat interfaces;
- make long-running tests faster, resumable, and easier to recover after provider or
  page failures;
- expand deterministic evidence and make report retention and deletion clearer;
- expand the local benchmark with reproducible authorized target fixtures;
- improve provider compatibility, release packaging, and installation diagnostics;
- publish a tested compatibility matrix for supported browser UI patterns.

## Later

- review automation for signed community strategy-pack pull requests;
- trace and tool-call evidence through standard telemetry formats;
- recorded multi-step flows for complex authenticated applications;
- regression runs and CI-friendly report formats;
- optional collaboration features that preserve local-first deployment.

## Recently shipped

- FrameFuzz matched framing campaigns with versioned templates, per-case fresh-context
  gates, Core and Direct execution, reproducible ordering, differential conclusions,
  report comparison, scenario schema v3, and vulnerable/safe local controls;
- reviewed local learning, frozen evaluation, promotion gates, and signed strategy
  packs without automatic cloud ingestion.

## Principles

- The operator defines the target and authorizes every run.
- Model output cannot create new browser operations or confirm a finding by itself.
- Credentials and captured evidence stay local unless the operator explicitly chooses
  an external provider.
- New capabilities must produce reviewable, reproducible evidence.

Feature requests and implementation proposals belong in
[GitHub Issues](https://github.com/whoishacked/stealth_prompt/issues).
