# Incremental migration plan

This plan deliberately avoids a rewrite. Each milestone should be a separate reviewable change (or a short series of changes when marked large), leave the repository runnable, and add tests before removing a legacy path. File names below are proposed; exact splits may change during implementation without changing the component boundaries in `target-architecture.md`.

## Milestone 1 — Repository cleanup and characterization tests

**Objective:** Establish a reliable Python package/test baseline and pin down current behavior before moving responsibilities.

**Affected files:** `requirements.txt`, new `pyproject.toml`, new `requirements-dev.txt` if needed, `main.py`, `.gitignore`, new `tests/unit/test_config_loader_legacy.py`, `tests/unit/test_prompt_db_legacy.py`, `tests/unit/test_llm_client_legacy.py`, `tests/unit/test_penetration_tester_legacy.py`, and test fixtures.

**Proposed design:** Convert `requirements.txt` to UTF-8 and reduce it to intentional direct runtime dependencies; declare supported Python versions and an installable console entry point. Add pytest and lightweight lint/type configuration. Write characterization tests around environment substitution, configuration validation, payload cleanup, refusal/repetition heuristics, prompt-chain migration/replay, report serialization, and the single-test duplicate-result bug. Mock network/browser/terminal boundaries rather than contacting a target. Fix only unambiguous defects: duplicate append, error status flattening where testable, and the `chain_id`/`id` log typo. Preserve the old CLI.

**Tests:** All new characterization tests; `python -m compileall`; CLI `--help`; install in a clean virtual environment using the supported Python matrix.

**Acceptance criteria:** A clean checkout installs through documented commands; `pytest` runs without a browser, model, or network; existing documented legacy commands still parse; direct dependencies and supported Python versions are explicit; known current quirks are either captured or corrected with release notes.

**Compatibility risks:** Reducing a transitive freeze may expose environments that relied on undeclared packages. Correcting duplicate results changes legacy output cardinality. Enforce no broader behavior changes in this milestone.

**Estimated complexity:** medium.

## Milestone 2 — Core domain and result models

**Objective:** Replace ad hoc result/config dictionaries at new boundaries with versioned typed values.

**Affected files:** new `src/stealth_prompt/core/models.py`, `src/stealth_prompt/config/models.py`, `src/stealth_prompt/config/validation.py`, `src/stealth_prompt/reporting/serialization.py`, tests under `tests/unit/core/`, and compatibility conversion helpers; no removal of legacy modules.

**Proposed design:** Add enums for final status and stop reason plus frozen dataclasses for `Scenario`, `RunContext`, `TargetSession`, `TargetResponse`, `PlannerDecision`, `OracleEvidence`, `TurnRecord`, `RunResult`, `ArtifactRef`, and `ExperimentSummary`. Define JSON-safe primitives and explicit `schema_version`. Keep raw sensitive bodies in artifacts and use references/digests. Add deterministic `to_dict`/`from_dict` functions rather than serializing class internals. Introduce structured `StealthPromptError` subclasses with safe messages and private details.

**Tests:** Construction invariants, UTC timestamp serialization, enum round trips, unknown/missing version handling, artifact path safety, result serialization, redaction markers, and golden JSON fixtures.

**Acceptance criteria:** A representative confirmed, not-detected, inconclusive, and error result round-trips identically; unknown schema versions fail clearly; no secret value appears in sanitized configuration snapshots; legacy results can be read by a limited compatibility reader or fail with an actionable message.

**Compatibility risks:** Status semantics intentionally expand beyond `success`/`completed`. Consumers of old JSON need a documented mapping; do not overwrite old result files.

**Estimated complexity:** medium.

## Milestone 3 — Target adapter interface and engine seam

**Objective:** Make target communication replaceable without changing attack strategy or result handling.

**Affected files:** new `src/stealth_prompt/adapters/base.py`, `src/stealth_prompt/adapters/factory.py`, `src/stealth_prompt/core/engine.py` skeleton, `src/stealth_prompt/adapters/legacy_selenium.py`, tests with a fake adapter, and small compatibility changes to `src/penetration_tester.py`.

**Proposed design:** Add the async `TargetAdapter` protocol and factory plus a fake in-memory adapter. Wrap `WebAutomation` in a transitional adapter using `asyncio.to_thread` or keep it exclusively behind the legacy runner; do not make Selenium a new public adapter name. Move lifecycle and response normalization into an engine seam while leaving existing payload/judge behavior behind adapters until later milestones. Centralize session ownership and guarantee `close_session`/`close` on cancellation and exceptions.

**Tests:** Adapter contract tests, lifecycle order, cleanup after initialization/send failures, session isolation, response truncation flags, cancellation, and a fake-adapter smoke run.

**Acceptance criteria:** The new engine can complete a one-turn run using only a fake adapter; its code imports no Selenium/Playwright/HTTP client; adapter failures produce typed error results and cleanup always runs; legacy Selenium remains usable through its documented path.

**Compatibility risks:** Introducing async boundaries may complicate embedding in an already-running event loop. Keep one `asyncio.run` call at the CLI edge and expose an async library API.

**Estimated complexity:** medium.

## Milestone 4 — HTTP target adapter

**Objective:** Implement the first fully supported black-box target adapter with deterministic local integration coverage.

**Affected files:** new `src/stealth_prompt/adapters/http_api.py`, `src/stealth_prompt/config/http_api.py`, `src/stealth_prompt/core/templating.py`, dependency metadata, `tests/unit/adapters/test_http_api.py`, and `tests/integration/test_http_api_adapter.py` with a local fixture server.

**Proposed design:** Use an async HTTP client with an isolated cookie jar per session. Support method/URL/headers/cookies, exactly one of JSON/form/text bodies, allowlisted `{{payload}}` and session variables, strict environment/file secret references, target conversation variables, JSONPath/header capture, JSONPath/text response extraction, structured timeouts, conservative explicit retries, verified TLS, optional CA bundle, opt-in insecure TLS, proxy, response byte caps, and minimal SSE `data:` collection. Never use `eval`, Jinja expressions, or arbitrary Python. Redact authorization/cookie headers in errors and artifacts.

**Tests:** Template substitution and missing-variable errors; JSON/form requests; cookies; generated and captured conversation IDs; JSONPath success/failure; text extraction; SSE split chunks/done/idle timeout; retryable 429/503 with `Retry-After`; non-retryable POST default; TLS configuration validation; proxy configuration mapping; response truncation; redacted diagnostics.

**Acceptance criteria:** A local mock endpoint supports two-turn conversation state and both JSON and SSE responses; integration tests send a synthetic payload, extract its answer, and record normalized transport metadata; TLS verification defaults to true; unresolved secrets prevent network access; no real credentials appear in fixtures.

**Compatibility risks:** The old top-level `http` block was not functional target support, so the new schema must not silently consume it. POST retries can duplicate requests; defaults must be off unless an idempotency policy is explicit.

**Estimated complexity:** large.

## Milestone 5 — Playwright UI adapter

**Objective:** Replace Selenium for new scenarios with a testable declarative browser flow and first-class artifacts.

**Affected files:** new `src/stealth_prompt/adapters/playwright_ui.py`, `src/stealth_prompt/adapters/playwright_flow.py`, `src/stealth_prompt/config/playwright_ui.py`, dependency/installation docs, browser fixtures, and `tests/integration/test_playwright_ui_adapter.py`; retain `src/web_automation.py` for legacy compatibility.

**Proposed design:** Use Playwright async Chromium with one isolated context/page per session. Implement allowlisted start and message steps: `goto`, `fill`, `click`, `press`, `wait_for`, and `extract`, with optional iframe scope. Support headed/headless mode, storage state, manual-login pause, action/navigation/response timeouts, a stable-text quiet period for streamed DOM responses, last/new element extraction, screenshots, trace, HAR, filtered network metadata, proxy, and explicit `ignore_https_errors`. Save artifacts below the run directory using safe generated names. Disable downloads and unexpected permissions by default. Do not support arbitrary code steps or claim arbitrary-site automation.

**Tests:** Flow-schema validation, unknown-step rejection, variable substitution, iframe scope, new-response correlation, streaming DOM stabilization, timeout/error screenshots, trace/HAR creation, context reset, storage-state load/save without embedding it in results, proxy/TLS option mapping, and cleanup of browser processes. Run against the local demo UI, not an external site.

**Acceptance criteria:** The tested local UI supports at least two turns; vulnerable-mode extraction creates `result.json`, a screenshot, and a readable trace reference; a second session starts clean; TLS-insecure and manual-login paths require explicit settings; Chromium is the only advertised tested engine.

**Compatibility risks:** Selenium selectors/config do not map one-to-one to Playwright locators and flow steps. Provide a hand-written migration example; keep the legacy runner through at least one release. Traces/HAR/storage state can be highly sensitive and need opt-in capture plus restricted files.

**Estimated complexity:** large.

## Milestone 6 — Deterministic success oracles

**Objective:** Make success evidence explicit and deterministic before adding autonomous planning.

**Affected files:** new `src/stealth_prompt/oracles/base.py`, `deterministic.py`, `callback.py`, oracle factory/config models, engine integration, `tests/unit/oracles/`, and callback integration fixtures.

**Proposed design:** Implement exact canary, expected fragment, regex, forbidden value, protected-document fragment, and local callback receipt. Each returns typed `OracleEvidence` with source location, match digest, safe preview policy, and strength. Compile/validate regexes before execution and apply input-size/time safeguards. The callback oracle binds to loopback by default, uses an unguessable per-run token, and shuts down with the run. Evaluate deterministic oracles after each response and stop immediately on confirmed evidence. Define status precedence centrally. Add optional LLM judge only as a disabled-by-default `likely`/`inconclusive` fallback; it cannot promote itself to confirmed.

**Tests:** Positive/negative exact, Unicode and case rules, regex flags and invalid patterns, overlapping fragments, protected-document fragments, callback token isolation/expiry, truncated responses, oracle errors, multi-oracle precedence, and serialization without leaking the protected value.

**Acceptance criteria:** A synthetic canary produces `confirmed` without an LLM; a refusal containing generic words such as “password” does not; unavailable/failed required oracles produce `inconclusive` or `error` per documented policy; console output does not print the canary by default.

**Compatibility risks:** Results will differ from the current LLM-only heuristic and may be more conservative. Saved `PromptDB` responses must not automatically become deterministic truth; offer import as explicit expected fragments only after user review.

**Estimated complexity:** medium.

## Milestone 7 — Static and autonomous multi-turn engine

**Objective:** Separate message selection from target transport and add a bounded adaptive planner with structured output.

**Affected files:** new `src/stealth_prompt/strategies/base.py`, `static.py`, `adaptive.py`, `providers/base.py`, `openai_compatible.py`, `ollama.py`, full `core/engine.py`, disclosure policy, and tests with fake strategies/providers.

**Proposed design:** Implement `StaticSequenceStrategy` and `AdaptiveStrategy`. Providers accept only a bounded `PlannerRequest` and return strict JSON fields `next_message`, `reasoning_summary`, `stop`, and `success_claimed`, plus usage. Strip/reject extra output with one bounded repair attempt. The engine owns maximum turns/duration, response and payload sizes, repeated payloads, repeated/near-identical target responses, rate-limit budget, provider token/cost caps, target unavailability, deterministic early success, and cancellation. Add explicit `none`/`redacted`/`full` target-data sharing policy; default to `none`, which makes adaptive external planning invalid until opted in. Never request/store chain-of-thought.

**Tests:** Static exhaustion; deterministic fake adaptive plans; malformed planner JSON; duplicate payloads; repeated responses; refusal progression; max turn/time/token/cost; target 429/unavailable; deterministic early stop; planner stop; claimed success without evidence; context minimization and redaction; provider usage aggregation.

**Acceptance criteria:** Identical fake inputs produce identical transcripts; every loop has at least one enforced finite bound; deterministic evidence stops before another provider call; adaptive runs record exactly what sharing policy was used; external providers are never contacted under `none`.

**Compatibility risks:** Existing hard-coded prompt generation may produce different attacks when moved behind a strategy. Preserve its useful wording as a versioned default planner prompt, not as hidden behavior. Provider APIs and model structured-output capabilities vary, so parsing must remain provider-neutral.

**Estimated complexity:** large.

## Milestone 8 — Repetitions, artifact store, reporting, and CLI

**Objective:** Turn individual attacks into reproducible experiments with a quiet, scriptable CLI.

**Affected files:** new `src/stealth_prompt/core/runner.py`, `reporting/store.py`, `reporting/summary.py`, `cli.py`, packaging entry point, legacy `main.py` wrapper, and tests under `tests/unit/reporting/` and `tests/integration/test_cli.py`.

**Proposed design:** Add `validate`, `run`, `list-adapters`, `list-oracles`, and `report`. CLI `--repetitions` overrides scenario execution. Create `results/<timestamp>-<target>-<experiment-id>/summary.json` plus immutable `run-NNN/` directories. Write files atomically with restrictive permissions; large evidence remains separate and content-addressed where useful. Default console output shows warning/status/count/path only; verbose output stays redacted. Use documented exit codes for validation failure, execution errors, disclosure found, no disclosure, and mixed/inconclusive outcomes. Warn and require a confirmation/explicit noninteractive acknowledgment before non-loopback targets. Default concurrency is one and rate is conservative.

**Tests:** Repetition override and precedence, per-run reset, shared-session warning, partial run after interruption, atomic write failure, file modes on POSIX, path traversal rejection, summary statistics/status rates/average turns/evidence types/cost, report reading, JSON stdout mode, quiet/verbose redaction, non-local acknowledgment, and exit codes.

**Acceptance criteria:** `stealth-prompt validate scenario.yaml` makes no target/model calls; five fake runs create five separate results plus a correct summary; an interrupted experiment preserves completed and partial runs; machine-readable output is stable; console output contains no target response by default.

**Compatibility risks:** Users may parse legacy filenames and report text. Keep `python main.py` and a legacy result reader temporarily, but do not merge legacy and new files into one undocumented schema. Exit code for “confirmed disclosure” must be chosen carefully for CI (document whether it is a finding code rather than an execution failure).

**Estimated complexity:** large.

## Milestone 9 — Local intentionally vulnerable demo and end-to-end tests

**Objective:** Provide a credential-free, deterministic proof that both adapters, oracles, repetitions, and artifacts work.

**Affected files:** new `examples/local-demo/` server, HTML/JavaScript UI, vulnerable and safer scenarios, synthetic protected document/canary fixture, `tests/e2e/`, and demo instructions.

**Proposed design:** Use a minimal local Python HTTP server unless requirements prove a small test-only framework materially simpler. Expose a JSON chat endpoint and a browser UI that calls it. Vulnerable mode deliberately concatenates hidden instructions containing a synthetic run-safe canary and follows a deterministic injection phrase; safer mode keeps the protected value out of model-visible/output paths and returns a refusal. Maintain per-conversation state so a deterministic fake attacker can demonstrate multi-turn extraction. Support a simple SSE option for HTTP tests. Bind only to loopback and use no external model or credentials.

**Tests:** Vulnerable HTTP and UI scenarios reach `confirmed`; safer mode reaches `not_detected`; repeated runs preserve separate transcripts; session reset prevents cross-run state; screenshot and trace references exist; JSON results validate; local callback fixture, if included, accepts only the matching token.

**Acceptance criteria:** One documented command starts/runs the demo locally; the default test suite uses the fake planner and no paid/network service; vulnerable mode is consistently confirmed by a synthetic deterministic oracle; safer mode has no confirmed evidence; artifacts can be opened and hashes match.

**Compatibility risks:** A deliberately vulnerable app can be copied or exposed accidentally. Bind loopback, label it clearly, avoid realistic credentials, and never package it as a production server.

**Estimated complexity:** medium.

## Milestone 10 — Documentation and release preparation

**Objective:** Align public claims with tested behavior and prepare the smallest focused release.

**Affected files:** `README.md`, `docs/architecture.md`, target configuration guides, adapter/oracle extension guides, sensitive-data and authorization guides, limitations, changelog/release notes, example scenarios, packaging metadata, and CI configuration.

**Proposed design:** Rewrite the README around black-box prompt-injection disclosure testing: what it is/is not, supported/tested targets, installation, first local test, deterministic canaries, static/adaptive modes, repeated-run interpretation, sensitive-data boundary, authorization, and limitations. Document Playwright flow creation with locator/codegen caveats and trace diagnostics. Document HTTP templates, JSONPath/SSE limits, TLS/proxy opt-ins, adapter/oracle protocols, and legacy migration/breaking changes. Add CI for unit tests plus a gated Playwright Chromium integration job.

**Tests:** Execute every documented local command in CI where practical; validate all checked-in scenarios; link check; clean-package install; source distribution/wheel smoke test; verify no examples contain non-synthetic credentials or non-local targets.

**Acceptance criteria:** A new user can install and complete the local demo without credentials; every advertised feature has an automated test; unsupported WebSocket/arbitrary-site/other-browser claims are absent; authorization and external-provider data sharing are prominent; release notes identify the legacy deprecation and result-schema break.

**Compatibility risks:** Removing inaccurate README claims may reveal behavior users thought was supported. Treat that as correction, provide migration notes, and keep the legacy path only for the announced window.

**Estimated complexity:** medium.

## Recommended release cut

A smallest useful vertical-slice pre-release takes milestones 1–4 and 6, the static-strategy portion of milestone 7, the `validate`/`run`/restricted JSON/repetition subset of milestone 8, and the HTTP portion of milestone 9. It can test a local or authorized HTTP assistant repeatedly and confirm a synthetic canary without an external model. It should be labeled HTTP-only rather than implying browser support.

The first broadly useful named release should complete milestones 1–10, including the tested Playwright path and adaptive provider boundary. CLI-backed planners, WebSocket, non-Chromium browsers, automatic codegen-to-flow conversion, dashboards, databases, and distributed/high concurrency should remain later work.
