# Stealth Prompt product roadmap

## Product thesis

Stealth Prompt is an interactive AI security workbench for testing one exact AI
interaction in a real, authenticated browser session. It is human-led,
AI-assisted, local-first, and evidence-driven.

It is not a generic web vulnerability scanner and it must not claim that an LLM
opinion proves a vulnerability. The product loop is:

> Observe → hypothesize → generate → review → send → capture → verify → reproduce.

The product should feel like a focused "Repeater for AI-agent interfaces": faster
and more precise than moving text between a browser and a terminal, while keeping
the operator in control of scope and mutations.

## Principles

1. **Exact target over broad crawl.** The operator selects the interaction and the
   extension shows what it can touch.
2. **Evidence over confidence theatre.** `confirmed` requires a deterministic
   signal or explicit expert confirmation.
3. **Local-first by default.** Website data and provider credentials do not enter
   a Stealth Prompt cloud service.
4. **Progressive autonomy.** Payload-only, assist, guided, and bounded auto are
   distinct contracts, not visual presets.
5. **Reproducibility.** A useful finding includes the configuration, attack chain,
   raw evidence policy, hashes, and a replayable scenario.
6. **Extensibility at security boundaries.** Objectives and scorers may be added;
   page operations remain a closed allowlist.

## Competitive direction

Stealth Prompt should beat broad automatic tools on control, authenticated browser
context, privacy, reproducibility, and operator experience. It should not compete
on the raw count of bundled jailbreak prompts.

The durable differentiators are:

- precise bindings with explainable auto-suggestions and a manual picker fallback;
- Claude/Codex CLI sessions as first-class providers, in addition to API/local models;
- deterministic and human-verifiable findings;
- per-send review and bounded autonomy;
- evidence that a security engineer can attach to a ticket;
- no vendor cloud requirement.

## Target runtime architecture

Today the Core is required. The target architecture makes it optional only when
the selected provider needs no secret in the browser:

- **Planned extension-only:** deterministic page discovery/capture and loopback Ollama.
- **Extension + Core:** Claude/Codex CLI, API keys, evidence storage, replay,
  deterministic scorers, callbacks, traces and exports.

Permanent provider keys are not stored in the extension. Browser-side API keys
would be extractable by anyone who can inspect or compromise the extension and
conflict with provider key-safety guidance. A future serverless provider must
offer OAuth or a short-lived, narrowly scoped token; until then, API keys stay in
the local Core/OS credential store. This keeps the simple path simple without
turning an unsafe BYOK shortcut into a product promise.

The Core therefore earns its place as a local security engine, not a prompt
proxy. Its durable responsibilities are:

- persistent Claude/Codex CLI sessions and provider latency instrumentation;
- a credential broker that never sends long-lived secrets to extension pages;
- local evidence, replay, regression baselines and signed exports;
- deterministic scorers and an out-of-band callback receiver;
- optional OpenTelemetry/Langfuse trace ingestion for agent/tool/RAG evidence.

## Release 0.2 — commercial-grade community preview

Goal: a free product that installs, explains itself, and produces an artifact with
the quality expected from paid software.

### Product experience

- [x] Cohesive visual system and branded Side Panel.
- [x] Distinctive small-size extension mark aligned with the product UI.
- [x] At-a-glance workflow progress and current run status.
- [x] Progressive disclosure for setup and advanced controls.
- [x] Clear local/external data-sharing disclosure.
- [x] Clear warning before target content is sent to an external provider.
- [x] Actionable readiness checks; Start never fails silently.
- [x] Loading, latency, cancellation, refusal, and terminal states.
- [x] Suggested input/send/response candidates with **per-role** confidence,
  a bounded reason, on-demand highlight, and independent accept/replace.
- [x] Binding health indicator (healthy / revalidating / needs review /
  unsupported) that revalidates after reload, same-origin navigation, SPA
  document replacement, panel reopen, and immediately before every mutation.
- [x] Auto pauses on a potential finding for explicit confirm-or-continue review.
- [x] Built-in guided demo (`stealth-prompt demo`) and a measured
  less-than-five-minute first-success path.
- [x] Guided connection → AI → target → interaction → run flow, including a
  non-sending draft fill that verifies the selected input.
- [x] Optional session-only direct OpenAI and Anthropic API mode with live model
  discovery, explicit browser-key warning, cancellation, and no credential storage.

### Security methodology

- [x] Versioned, closed objective catalogue covering core LLM and agent risks.
- [x] Direct/indirect injection, disclosure, goal hijacking, RAG, memory,
  excessive-agency, tool misuse, and approval-bypass objectives.
- [x] Deterministic confirmation boundary.
- [x] Versioned scenario files (schema v1) with export, import preview, origin
  mismatch warning, and replay that always revalidates.
- [x] Deterministic scorer set: exact/fragment, regex, structured JSON field,
  DOM assertion, navigation/origin assertion, and explicit human confirmation —
  each with scorer id, status, bounded evidence, hash, deterministic flag,
  reason, timestamp and turn id, surfaced in both JSON and HTML reports.
- [ ] Trace, tool-call and callback scorers. **Prerequisite:** an evidence
  source for them must exist first — OpenTelemetry/Langfuse trace ingestion or
  the OAST callback listener below. Until one lands, an interface for them
  would be an empty promise, so none is defined.
- [ ] Semantic scorer as a first-class, separately-reported signal.
  **Prerequisite:** it must stay capped at `potential`; the report needs to show
  it beside deterministic results without implying it can confirm.
- [ ] Repetition metrics: pass@k, turns-to-success, instability and abstention.
  **Prerequisite:** local run history (Release 0.3) to aggregate over.

### Evidence and trust

- [x] Self-contained HTML report next to the machine-readable JSON evidence.
- [x] Report includes scope, configuration, attack chain, hashes, verdict
  provenance, next steps, and timeline.
- [x] Privacy policy, security policy, contribution guide, changelog and roadmap.
- [x] Core dependency is present in the default installation.
- [x] TypeScript lint/test/build runs in CI alongside Python and browser E2E.
- [ ] Signed release archives, SBOM and provenance attestations.
- [x] [Chrome Web Store listing](https://chromewebstore.google.com/detail/stealth-prompt/genafpggpdjagohhbngddncbanhpcdpm),
  disclosures, screenshots and support URL.
- [ ] Native Core installers and automatic update checks.

## Release 0.3 — professional local workbench

Goal: repeatable assessments rather than isolated sessions.

Scenario files and the deterministic scorer set shipped in 0.2 and are the
foundation the rest of this release builds on. The remaining items are
sequenced so each one has a real evidence source before it is built.

- Opt-in Core strategy memory that turns reviewed, sanitized run outcomes into
  local reusable tactics. Direct API keeps a read-only built-in catalogue and
  never learns from browser-local reports automatically.
- Projects, targets, scope notes and authorization records.
  **Prerequisite:** a Core-owned local store with atomic writes, owner-only
  permissions, versioned metadata and configurable retention. Scenario export
  already produces the per-run artifact such an index would point at.
- Searchable local run history with retention and deletion controls.
  **Prerequisite:** the store above; deletion must state exactly which
  artifacts it removes.
- Scenario variants and baseline comparison.
  **Prerequisite:** run history, so a baseline has something to compare to.
- Custom objective templates, attack packs and scorer packs.
- Redaction preview before provider submission.
- Optional encrypted artifact vault and signed evidence manifest.
- HTML/PDF/JSON/SARIF/JUnit exports.
- Compatibility matrix for representative chat UIs.
- Better capture for iframes, open Shadow DOM, `contenteditable`, streaming,
  virtualized chats, attachments and SPA route changes.
- Persistent provider sessions, first-token streaming and per-phase latency.
  **Prerequisite:** none technically, but first-token latency must only be
  reported for adapters that actually expose it — a fabricated number is worse
  than an absent one.
- Redaction preview before provider submission.
  **Prerequisite:** the preview must not persist raw target text in extension
  storage, so it is render-only and derived from the Core's redaction pass.
- Extension-only Ollama mode, restricted to a fixed loopback endpoint policy.
  **Prerequisite:** an honest statement of what is unavailable without the
  Core. Evidence history, replay and deterministic scoring are Core policy;
  duplicating them in TypeScript would create a second, weaker implementation
  of the rules that decide whether a finding is real. The first slice is
  therefore payload generation only, with the Core-only features named in the
  UI rather than silently missing.
- OAST callback listener for SSRF, data exfiltration and unsafe tool-use evidence.
  **Prerequisite:** a decision on where the listener runs; a local-only
  listener cannot observe an egress a remote target makes.

## Release 1.0 — team-ready local-first product

Goal: teams can standardize and govern assessments without surrendering target
data to a mandatory SaaS backend.

- Shared signed test packs and policy baselines.
- Roles, approvals and immutable audit records.
- CI policy gates and release-to-release regression dashboards.
- Optional Langfuse/OpenTelemetry/custom trace ingestion for tool calls, RAG
  retrieval and agent state.
- Multi-account and authorization-boundary scenarios.
- Flow recorder with semantic checkpoints for OAuth and multi-step applications.
- Optional team control plane plus fully self-hosted deployment.
- SSO/SAML, RBAC, retention policies and enterprise support lifecycle.

## Explicit non-goals

- Generic SQL injection, XSS, port scanning or broad DAST crawling.
- Inventing browser selectors or executable operations from model output.
- Reporting a critical finding from an LLM judge alone.
- Uploading page content to a Stealth Prompt service by default.
- Hidden autonomous testing without reviewed scope and explicit limits.

Web vulnerabilities belong when they are an outcome of AI-agent behaviour—for
example unsafe HTML rendering, a tool-call SQL injection, SSRF, cross-tenant data
access, or an action performed without confirmation.

## Definition of Done for a public beta

- Clean checkout → working Core and extension without undocumented steps.
- First demo finding in under five minutes.
- No repository checkout or npm toolchain required for an end user.
- Every external data transfer is disclosed before the run.
- Every finding can be replayed from an exported scenario.
- No LLM-only result is labelled `confirmed`.
- HTML and JSON reports agree on verdict, evidence and configuration.
- Release is signed, includes an SBOM, and passes Python, TypeScript and real-browser CI.
- Privacy, retention, deletion, security disclosure and support paths are public.
- A published compatibility matrix states exactly which UI patterns are supported.

## Product metrics

- Time to first successful test.
- Binding success and response-capture success by target UI pattern.
- Provider startup, first-token, generation, capture and evaluation latency.
- Scenario replay success and result stability.
- Confirmed/potential/inconclusive rates and operator-confirmation rate.
- Cancellation success and failed-session rate.
- Report export rate and number of findings reproduced by a second operator.
