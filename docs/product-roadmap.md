# Product roadmap

This roadmap describes direction, not release commitments. Stealth Prompt remains
focused on authorized testing of selected AI interactions in a real browser session.

## Current priorities

- improve element detection and response capture across common chat interfaces;
- make long-running tests faster, resumable, and easier to recover after provider or
  page failures;
- expand deterministic evidence and make report retention and deletion clearer;
- improve provider compatibility, release packaging, and installation diagnostics;
- publish a tested compatibility matrix for supported browser UI patterns.

## Later

- reusable objective, scenario, and scorer packs;
- trace and tool-call evidence through standard telemetry formats;
- recorded multi-step flows for complex authenticated applications;
- regression runs and CI-friendly report formats;
- optional collaboration features that preserve local-first deployment.

## Principles

- The operator defines the target and authorizes every run.
- Model output cannot create new browser operations or confirm a finding by itself.
- Credentials and captured evidence stay local unless the operator explicitly chooses
  an external provider.
- New capabilities must produce reviewable, reproducible evidence.

Feature requests and implementation proposals belong in
[GitHub Issues](https://github.com/whoishacked/stealth_prompt/issues).
