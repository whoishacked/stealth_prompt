# Claude Development Prompt: Extension-First Stealth Prompt MVP

You are working in the existing Stealth Prompt repository.

Your task is to implement the first production-quality, extension-first version of Stealth Prompt.

Do not stop at an architecture proposal or scaffolding. Inspect the existing implementation, make the changes, run the tests, perform real-browser verification, and report only what actually works.

## Repository context

The current repository already contains:

- agent adapters for Fake, Claude CLI, Codex CLI, Ollama, and OpenAI;
- provider/model discovery and health checks;
- a Python-owned attack engine and state machine;
- StopPolicy, target bindings, deterministic oracles, artifacts, redaction, and a typed broker protocol;
- a WorkbenchSession that launches an isolated Playwright Chromium;
- a temporary generated MV3 extension under:
  `src/stealth_prompt/workbench/extension/`;
- an injected in-page dock implemented in `content.js`;
- extensive unit and integration tests;
- a deliberately vulnerable local demo target.

Relevant existing modules include:

- `src/stealth_prompt/agents/`
- `src/stealth_prompt/workbench/engine.py`
- `src/stealth_prompt/workbench/session.py`
- `src/stealth_prompt/workbench/state.py`
- `src/stealth_prompt/workbench/binding.py`
- `src/stealth_prompt/workbench/broker.py`
- `src/stealth_prompt/workbench/protocol.py`
- `src/stealth_prompt/workbench/operations.py`
- `src/stealth_prompt/workbench/artifacts.py`
- `src/stealth_prompt/workbench/extension/`
- `tests/`
- `examples/local-demo/`

Read the current source, tests, README, and architecture/security documentation before changing anything.

The working tree is already dirty. Preserve all existing user changes. Do not stage or commit anything.

## Product decision

Stealth Prompt is no longer primarily a CLI-launched browser Workbench.

The main product is an installable browser extension running in the user’s normal Chrome/Chromium profile.

The extension is not:

- an automatic website scanner;
- a crawler;
- a general-purpose autonomous pentester;
- a Burp or ZAP replacement;
- an OAuth/network-flow analyzer in this milestone.

It is a human-guided, browser-native AI red-team assistant for testing one explicitly selected UI interaction.

The user selects a concrete AI chat or form interaction:

- input element;
- send control;
- response container.

The assistant then:

1. understands the selected interaction and existing conversation;
2. proposes a targeted security-testing payload;
3. explains its hypothesis and expected signal;
4. waits for operator approval;
5. sends only through the selected interaction;
6. captures the resulting output;
7. analyzes the output;
8. proposes a next step.

The product must not scan unrelated forms, navigate autonomously, or interact with arbitrary controls.

A good concise product description is:

> An open-source browser-native AI red-team assistant that helps an operator test a selected AI interaction, generate targeted payloads, analyze responses, and iteratively continue the assessment.

## Decisions that are already made

Do not reopen these decisions unless the current platform makes one technically impossible.

1. The browser extension is the primary UI and product.
2. It runs in the user’s ordinary browser, not only in a Chromium process launched by Python.
3. Use a Chrome Manifest V3 Side Panel for the persistent UI.
4. Do not inject the complete product UI into the target page.
5. The content script is an ephemeral DOM observer/executor only.
6. Python remains as a local companion/core for Claude CLI, Codex CLI, Ollama, OpenAI, planning, evaluation, artifacts, and policy enforcement.
7. There is no mandatory cloud backend.
8. The CLI must not open a browser for the normal extension workflow.
9. The default workflow requires approval before every send.
10. The first payload is generated automatically. The user must not need to type an initial instruction such as “generate a prompt-injection payload.”
11. Provider and model are selected in the Side Panel.
12. The extension must preserve settings, bindings, and the active assistant session across reloads and same-origin navigation.
13. Workbench may remain temporarily as a deprecated compatibility and test path, but it is no longer the main product.
14. Do not implement OAuth flow recording, general network capture, SQLi, XSS, crawling, or a plugin marketplace in this milestone.
15. Do not make direct AI provider calls from the extension in this milestone. Provider credentials and executable paths remain in the local Python core.
16. Do not add `chrome.debugger`, `webRequest`, `<all_urls>`, or equivalent broad permissions.

## Primary goal

Deliver one complete vertical slice:

```text
Install/load extension
→ open Side Panel
→ pair with local Core
→ choose provider and model
→ select input/send/response elements
→ save the interaction binding
→ start an assistant session
→ automatically receive a first payload proposal
→ review/edit/approve it
→ send it through the selected UI
→ capture the response
→ receive an evaluation and suggested next step
→ reload or navigate within the origin
→ reopen the same usable session
→ export evidence
```

This flow must work end to end with the built-in Fake provider and the local demo target without contacting any external service.

## User experience

### First-time experience

The user should be able to run:

```bash
stealth-prompt serve
```

The command must:

- start only the local Core;
- bind only to loopback;
- never open a browser;
- print a concise connection status;
- provide a secure pairing method for the extension;
- expose provider health and model discovery through the existing authoritative provider registry;
- exit cleanly on Ctrl+C.

Example output:

```text
Stealth Prompt Core
Listening on 127.0.0.1:17371
Pairing code: ABCD-EFGH
Open the Stealth Prompt browser extension to connect.
```

Do not expose API credentials, access tokens, command lines containing secrets, or raw provider output.

### Side Panel structure

Implement a clear, compact Side Panel containing approximately:

```text
Stealth Prompt

Connection
● Local Core connected

Target
example.test/chat
[Select interaction]

Interaction
✓ Input
✓ Send control
✓ Response container
[Rebind] [Validate]

AI
Provider: Codex CLI
Model: gpt-5.6-sol
Sharing: Redacted

Mode
Assist

Objective
Prompt injection / instruction disclosure
Advanced instruction: optional

Proposal
Hypothesis
Payload
Expected signal
Risk

[Regenerate] [Edit] [Copy] [Approve and send]

Evaluation
Observed signals
Verdict
Suggested next step

[Generate next proposal] [Stop] [Export]
```

The exact visual design may differ, but it must be understandable without reading documentation.

### Supported extension modes

Expose these modes:

1. `payload_only`
   - generates proposals;
   - may capture a response only when explicitly requested;
   - never fills, clicks, presses Enter, or sends anything;
   - browser executor must reject mutating operations in this mode.

2. `assist` — default
   - operator explicitly requests or regenerates each proposal;
   - operator approves every send;
   - response capture and evaluation are automatic.

3. `guided`
   - after a response is evaluated, the next proposal may be generated automatically;
   - every send still requires explicit operator approval.

Do not expose an unattended automatic scan mode in the extension MVP.

The legacy Workbench `auto` mode may remain for compatibility, but it must not shape the new extension UX.

### First payload

Starting a test must automatically generate the first proposal from:

- selected objective;
- interaction metadata;
- optionally captured existing conversation;
- data-sharing policy;
- optional advanced instruction.

A free-form initial prompt must not be required.

Provide sensible objectives such as:

- prompt injection;
- hidden/system instruction disclosure;
- sensitive data disclosure;
- role or instruction hierarchy confusion;
- tool/action misuse;
- custom objective.

Use a safe default objective when the operator does not choose one.

### Readiness and errors

Never leave the operator with a mysteriously disabled button.

If the session cannot start, show a checklist such as:

```text
Cannot start:
- Connect to the local Core.
- Select the response container.
- Choose how target data may be shared.
```

A Start/New Test action may remain clickable and must explain missing requirements.

Show explicit states for:

- connecting;
- discovering providers;
- generating;
- waiting for approval;
- sending;
- waiting for response;
- evaluating;
- cancelled;
- timed out;
- provider refused;
- target binding invalid;
- Core disconnected.

Show elapsed time during model generation and provide a working Cancel control.

## Extension architecture

Create a standalone extension source tree that can be loaded unpacked without running Workbench.

A reasonable location is:

```text
extension/
  manifest.json
  src/
    sidepanel/
    service-worker/
    content/
    protocol/
    storage/
  tests/
  dist/
```

Use a small, maintainable, reproducible build.

Prefer:

- strict TypeScript;
- vanilla DOM components or another minimal approach;
- no large UI framework unless it provides a demonstrated benefit;
- a checked-in lockfile;
- no CDN or remote executable code;
- no `eval`, `new Function`, inline script, or `unsafe-eval`;
- no interpolated `innerHTML`;
- text rendered through safe DOM APIs such as `textContent`.

The built extension must contain a static Manifest V3 manifest. It must not depend on per-run manifest generation.

### Side Panel

The Side Panel owns the visible UI and may own the live Core connection while it is open.

It must reconnect and restore its view from persisted state after being closed and reopened.

### Service worker

The service worker routes trusted extension messages and manages:

- tab identity;
- document identity;
- permissions;
- content-script injection;
- persisted state;
- binding lookup;
- Side Panel/content script communication.

Do not depend on service-worker global variables for durable state.

### Content script

The content script must not contain the complete Side Panel UI.

It may implement:

- element-picking overlay;
- locator generation;
- locator resolution;
- visual highlighting;
- fill;
- click;
- allowed key press;
- response extraction;
- MutationObserver-based response stabilization;
- narrowly scoped conversation extraction;
- operation result reporting.

It must not:

- connect directly to the Python Core;
- choose providers or models;
- choose what payload to send;
- execute arbitrary JavaScript from the model;
- accept commands from the target page;
- navigate the browser;
- read cookies or browser storage;
- collect unrelated page content.

The target page is hostile. Do not use untrusted page events as authoritative commands.

### Permissions

Use least privilege:

- `sidePanel`;
- `storage`;
- `scripting`;
- `activeTab`;
- optional per-origin host permissions only when required.

Do not request `<all_urls>`.

When saving a persistent interaction binding, request permission only for the selected origin. Clearly explain the permission to the operator.

Do not run content scripts on unrelated origins.

## Persistence model

The current navigation failure must be fixed by design.

Separate:

- extension installation identity;
- assistant session identity;
- Chrome tab ID;
- document ID;
- origin;
- interaction binding ID;
- turn ID;
- operation ID;
- capture ID.

A new document after reload must not be treated as the entire assistant session disappearing.

Persist:

- provider selection;
- requested model;
- effective model;
- mode;
- sharing policy;
- objective;
- reviewed interaction bindings;
- active session summary;
- proposal/evaluation timeline;
- Core pairing metadata that is safe to persist.

Use:

- `chrome.storage.session` for suitable active runtime state;
- `chrome.storage.local` for reviewed settings and bindings;
- Python ArtifactStore for complete session artifacts.

Do not store:

- cookies;
- access tokens from the target;
- page localStorage/sessionStorage;
- passwords;
- raw API keys;
- provider credentials.

Bindings should be keyed by origin plus an explicit path strategy. Validate bindings against the live page after navigation. If a locator is ambiguous or missing, pause and request rebinding instead of acting on the wrong element.

## Local Core

Add a first-class command:

```bash
stealth-prompt serve
```

Do not make `serve` depend on Playwright or launch Chromium.

Refactor rather than duplicate the existing logic.

Reuse:

- provider registry;
- Claude/Codex/Ollama/OpenAI/Fake adapters;
- planner;
- state machine concepts;
- StopPolicy;
- redaction;
- ArtifactStore;
- deterministic oracles;
- typed operations;
- protocol validation.

It is acceptable to introduce a clearer package such as:

```text
src/stealth_prompt/core/
```

but avoid a high-risk wholesale rename unrelated to the vertical slice.

The Core must be authoritative for:

- provider executable paths;
- provider endpoints;
- credentials;
- model validation;
- assistant proposal generation;
- evaluation;
- operation policy;
- limits;
- artifacts.

Nothing received from the extension may select an arbitrary executable, filesystem path, endpoint, or shell command.

### Core connection and pairing

For the MVP, an authenticated loopback WebSocket is acceptable.

Requirements:

- bind only to `127.0.0.1` or `::1`;
- use a stable configurable default port suitable for extension discovery;
- refuse non-loopback binding unless a future explicit secure design exists;
- validate the WebSocket Origin;
- require an explicit pairing step;
- exchange the short-lived pairing code for a random scoped credential;
- pin the paired extension identity where practical;
- store tokens only in trusted extension storage;
- support revocation/re-pairing;
- use constant-time token comparison;
- cap message size;
- validate every message;
- fail closed on unknown message types or fields;
- never expose pairing secrets to the target content script.

Document Native Messaging as a possible later transport, but do not make it a prerequisite for this MVP.

## Assistant proposal and evaluation contracts

Do not treat arbitrary model text as an executable browser instruction.

Introduce a typed proposal contract similar to:

```json
{
  "schema_version": 1,
  "proposal_id": "proposal-...",
  "objective": "instruction_disclosure",
  "hypothesis": "The target may reveal hidden instructions through a transformation request.",
  "payload": "…",
  "rationale": "…",
  "expected_signals": [
    "instruction-like content",
    "protected canary"
  ],
  "risk": "low",
  "provider": "codex",
  "requested_model": "…",
  "effective_model": "…"
}
```

Only the `payload` may be offered for insertion into the selected input.

The model must never provide:

- selectors;
- locator changes;
- executable operations;
- JavaScript;
- shell commands for execution;
- browser navigation instructions that are automatically followed.

Introduce a typed evaluation contract similar to:

```json
{
  "schema_version": 1,
  "evaluation_id": "evaluation-...",
  "verdict": "not_observed | potential | confirmed | inconclusive",
  "summary": "…",
  "observed_signals": [],
  "evidence_ids": [],
  "suggested_next_steps": []
}
```

An LLM opinion alone must not become `confirmed`.

Use `confirmed` only when:

- a deterministic configured oracle matches; or
- the operator explicitly confirms the finding.

Otherwise use `potential` or `inconclusive`.

### Provider refusals

A provider refusal must never be treated as a payload and sent to the target.

Detect and represent refusal as a separate outcome:

```text
Provider declined to create a proposal.
```

Allow the operator to:

- adjust the authorized objective;
- regenerate;
- edit a payload manually;
- choose another configured provider.

Do not attempt to bypass provider safeguards.

### Structured output parsing

Parsing must fail closed.

Handle:

- valid structured responses;
- fenced JSON where appropriate;
- streaming output;
- malformed output;
- refusal;
- timeout;
- cancellation;
- abandoned generation;
- stale turn output.

Never reuse malformed text as an executable payload automatically.

## Interaction execution

Continue the existing principle:

> The extension proposes/displays; Python decides policy; the content script executes one allowlisted operation at a time.

For `assist` and `guided` modes:

1. proposal is displayed;
2. operator may edit the payload;
3. the exact final text is shown;
4. operator presses `Approve and send`;
5. Core records approval;
6. content script fills the bound input;
7. content script performs the reviewed submit strategy;
8. response capture begins;
9. stable response is returned with correlation IDs;
10. Core evaluates it;
11. the timeline is updated.

Do not combine approval and an invisible payload mutation.

Record the hash or exact redacted representation of the approved payload so evidence shows what was actually sent.

### Response capture

Support common chat behavior:

- streaming text;
- multiple response nodes;
- reused response containers;
- disabled/enabled send button;
- DOM replacement;
- same text appearing in multiple turns.

Capture the response correlated to the current send, not an older matching response.

Use:

- pre-send response snapshot;
- MutationObserver;
- configurable stability window;
- bounded timeout;
- manual “Capture current response” fallback;
- visible capture status.

Do not use one unconditional fixed sleep as the primary completion detector.

## Data sharing

The assistant’s main value requires analyzing target output, but sharing must never be silent.

Before starting a session, require an explicit policy:

- `none`;
- `redacted`;
- `full`.

Recommended UI defaults:

- external provider: recommend `redacted`;
- local Ollama: explain that `full` remains local;
- never silently change the policy.

Before sending target-derived data to an external provider, allow the operator to preview what will be transmitted.

`none` must mean no target response is sent to the provider. In that mode, the UI must explain that adaptive response analysis is unavailable or limited.

Preserve bounded sizes and credential-shaped redaction.

## Performance

The existing experience has sometimes taken several minutes without useful feedback.

Improve perceived and actual latency:

- stream provider output into the proposal area where supported;
- show the current stage and elapsed time;
- make cancellation functional;
- do not start an agent when the selected action does not require one;
- do not start a provider merely for static planning under a no-sharing path;
- cache provider/model discovery for a reasonable TTL;
- reuse provider sessions where the provider protocol safely supports it;
- fence abandoned generations so stale output cannot appear in a later turn;
- use explicit per-stage timeouts;
- report which stage timed out.

Do not claim a cost limit is enforced when a provider reports no cost.

## Timeline for future flow analysis

Do not implement OAuth or request recording now.

However, avoid a dead-end data model by introducing a small versioned assistant timeline abstraction.

The MVP timeline may contain only events such as:

```text
session.started
interaction.bound
conversation.captured
proposal.generated
proposal.edited
proposal.approved
payload.sent
response.captured
evaluation.completed
session.stopped
```

Each event should have stable IDs, timestamp, source, and sanitized metadata.

Future events such as these may be documented but must not be implemented now:

```text
navigation.observed
request.observed
response.observed
auth.boundary_detected
```

Do not build a generic plugin framework merely for this future possibility.

## Security requirements

Preserve or strengthen the existing security decisions:

- authorized targets only;
- explicit scope;
- no arbitrary browser navigation;
- no shell invocation controlled by the model or extension;
- no model-controlled executable paths;
- no arbitrary JS execution;
- no `eval`;
- no remote extension code;
- no broad host permission;
- no target credentials in bindings;
- no API keys in extension frames;
- strict message-size limits;
- strict protocol versioning;
- correlation IDs on every asynchronous operation;
- restrictive artifact permissions;
- redaction before external sharing;
- safe DOM rendering;
- safe logging against terminal control characters;
- cancellation and turn fencing;
- stale-tab/document/operation rejection.

The content script and target page must never be trusted to modify:

- provider;
- model;
- executable path;
- API endpoint;
- data-sharing policy;
- session limits;
- operation allowlist;
- authorization state.

## Migration and compatibility

Do not delete the existing Workbench path immediately.

Instead:

1. add the standalone extension;
2. add `stealth-prompt serve`;
3. extract reusable Core functionality;
4. keep `stealth-prompt workbench` functioning where practical;
5. mark Workbench as deprecated in documentation;
6. make the extension workflow the primary README path;
7. keep the isolated browser path for compatibility, integration testing, and possible future CI.

Do not duplicate agent adapters or create a second incompatible protocol without a migration reason.

If protocol v2 is needed, version it explicitly and document compatibility.

## Documentation

Update or add:

- main README extension-first quickstart;
- local Core installation and `serve` usage;
- unpacked extension development instructions;
- provider/model behavior;
- permissions explanation;
- data-sharing behavior;
- architecture diagram;
- protocol/pairing documentation;
- threat model;
- migration notes from Workbench;
- troubleshooting:
  - Core not connected;
  - provider installed but unauthenticated;
  - model unavailable;
  - target binding invalid after navigation;
  - response capture timeout;
  - provider refusal;
  - extension permission revoked.

Document clearly that Stealth Prompt is an assistant for authorized, targeted testing, not an automatic scanner.

## Testing requirements

All tests must be offline unless explicitly marked as optional live tests.

### Python

Run and keep clean:

```bash
pytest -q
ruff check .
mypy src/stealth_prompt tests
```

Update obsolete tests intentionally rather than deleting coverage.

Add tests for:

- `stealth-prompt serve`;
- loopback-only binding;
- pairing and re-pairing;
- invalid Origin;
- invalid token;
- oversized and malformed frames;
- provider/model discovery through Core;
- proposal parsing;
- provider refusal;
- stale generation fencing;
- approval enforcement;
- payload-only mutation rejection;
- session restoration;
- artifact schema;
- timeline events;
- data-sharing policies;
- no secret leakage through repr, frames, or artifacts.

### Extension

Add a reproducible extension test toolchain and run:

```bash
npm ci
npm run lint
npm test
npm run build
```

Use equivalent command names only if documented.

Test:

- storage/reducer logic;
- protocol validation;
- Side Panel state restoration;
- readiness diagnostics;
- safe rendering;
- target binding serialization;
- locator validation;
- operation correlation;
- provider refusal rendering;
- no send without approval;
- payload-only rejection;
- reconnect after Core restart;
- same-origin reload/navigation restoration;
- hostile or malformed content-script frames.

### Real-browser integration

Use the Fake provider and local demo to execute real Chromium scenarios.

At minimum verify:

1. The standalone unpacked extension loads without Workbench.
2. The Side Panel can connect and pair with `stealth-prompt serve`.
3. Provider and model selection come from the Core.
4. The operator can select input, send, and response elements.
5. The binding is validated and saved.
6. Starting a session automatically produces the first proposal.
7. No payload is sent before approval.
8. Approved payload reaches the selected input only.
9. The correct new response is captured.
10. Evaluation appears with evidence.
11. Guided mode proposes the next turn but does not send it.
12. Payload-only mode leaves the page byte-for-byte unchanged where practical.
13. Reloading the target page does not erase settings or the assistant timeline.
14. Same-origin navigation reconnects to the session.
15. Invalid bindings pause safely and request rebinding.
16. Core disconnection produces a recoverable UI state.
17. A target page cannot forge a provider change or browser operation.
18. No external model or target is contacted by the test suite.

If Side Panel automation has Chromium limitations, keep the real extension installed and exercise the same extension page/runtime APIs. Document the exact limitation and retain as much real-browser coverage as possible.

Do not contact live Claude, Codex, OpenAI, Ollama, or a non-loopback target merely to complete the test suite.

Optional live smoke tests must remain opt-in.

## Acceptance criteria

The implementation is accepted only if all of these are true:

1. The extension can be installed independently of Workbench.
2. Normal use occurs in the user’s existing browser.
3. `stealth-prompt serve` never launches a browser.
4. Side Panel is the primary UI.
5. The in-page dock is not the primary UI.
6. Provider and model are chosen in Side Panel.
7. The local Core remains authoritative.
8. No API key is exposed to the extension.
9. The first proposal is generated automatically.
10. The user selects a specific interaction rather than a whole-site scan.
11. Every send requires explicit approval.
12. Payload-only mode cannot mutate the page.
13. Response output is captured and analyzed.
14. The assistant can propose a next targeted step.
15. Provider refusal is not treated as a payload.
16. Settings and bindings survive reload.
17. Active session state survives same-origin navigation.
18. Invalid/stale document operations are rejected.
19. No broad browser permissions are added.
20. Existing Workbench tests remain working or are intentionally migrated.
21. Offline unit and integration suites pass.
22. A real-browser Fake-provider demonstration passes.
23. Documentation describes what actually exists.
24. No files are staged or committed.

## Non-goals for this implementation

Do not implement:

- automatic crawling;
- whole-site scanning;
- OAuth flow recording;
- request/response interception;
- HAR capture;
- proxying;
- SQL injection testing;
- XSS testing;
- `chrome.debugger`;
- cloud accounts;
- hosted backend;
- team collaboration;
- telemetry;
- plugin marketplace;
- Firefox support;
- unattended extension auto-send.

Mention these only as future work.

## Working method

1. Inspect the repository and current tests.
2. Write a short implementation plan tied to concrete files.
3. Implement the complete vertical slice.
4. Keep changes incremental and reviewable.
5. Run focused tests during development.
6. Run the complete verification suite.
7. Perform real-browser verification with the Fake provider.
8. Inspect the final diff for security regressions and accidental unrelated changes.
9. Do not stage or commit.

Do not claim a scenario passed unless you actually ran it.

If a requirement cannot be completed, do not silently replace it with a stub. Implement everything else possible and report:

- the exact blocker;
- the affected files;
- what was verified;
- what remains unverified;
- the smallest next action required.

## Final response format

Report:

1. concise outcome;
2. resulting architecture;
3. exact user workflow;
4. important files added or changed;
5. security decisions;
6. tests and exact results;
7. real-browser scenarios actually executed;
8. remaining limitations;
9. manual commands for local verification;
10. confirmation that nothing was staged or committed.

Do not provide a speculative success summary. Base every statement on the final source and executed verification.
