# Security and safe-use considerations

Stealth Prompt is an authorized testing tool that intentionally handles adversarial input, authenticated target sessions, and potentially confidential target output. Its own safety model must protect those assets without preventing legitimate testing of internal and non-local systems.

## Authorization and product boundary

Users must test only systems they own or are explicitly authorized to assess. New runs should display this warning and require acknowledgment before contacting a non-loopback target; noninteractive CI needs an explicit flag or scenario acknowledgment. The result records that acknowledgment and a user-supplied, non-secret scope note, not legal conclusions.

The tool's supported purpose is black-box prompt-injection testing for protected-information disclosure through web UIs and HTTP APIs. It should not:

- scan source code, repositories, MCP servers, model/agent configuration, or secret managers;
- include credential theft, destructive actions, persistence, or real third-party exfiltration endpoints;
- probe unrelated AI risks merely because they can be tested with a prompt;
- discover or attack arbitrary internet targets;
- silently share target data with a planner/judge provider.

Examples and tests use synthetic canaries and a loopback intentionally vulnerable application. Callback examples bind to loopback and use per-run random tokens.

## Assets and trust boundaries

Protected assets include:

- target credentials, authorization headers, cookies, storage state, and authenticated browser profiles;
- disclosed system prompts, RAG fragments, cross-user data, internal IDs/metadata, and complete transcripts;
- attacker-model credentials and proxy credentials;
- screenshots, traces, HAR, network bodies, raw responses, callback logs, caches, and summaries;
- result integrity: the association between payload, target response, evidence, run, and target session.

The main boundaries are:

1. scenario/configuration file to the local process (trusted operator input, but still safely parsed);
2. local process to the target application;
3. target page/content to the browser and logs;
4. target transcript to a local or external attacker-model provider;
5. process memory to local artifact storage;
6. authenticated browser state to Playwright/Selenium debugging and trace tooling;
7. local callback listener to target-accessible network paths.

Configuration is operator-controlled, so the tool cannot prevent a determined operator from contacting an internal address. It must still warn, validate schemes, redact diagnostics, and never describe simple URL syntax checks as SSRF prevention. Arbitrary authorized internal targets are a legitimate use case.

## Current-state risks confirmed in the repository

### High: raw target data is printed and persisted

`WebAutomation` prints outbound prompts and up to 1,000 response characters; `PenetrationTester` prints the complete response on a positive LLM decision; `LLMClient` prints attacker-model inputs/outputs. Normal JSON/TXT results and `successful_prompts.json` store complete conversations. File creation relies on the user's umask rather than explicitly restricting modes. `output.save_responses` and `save_analysis` do not control the normal result path.

**Required control:** New default console output must contain only run status, counts, sanitized error codes, and artifact paths. Create result directories as `0700` and files as `0600` on POSIX where supported; use atomic writes; apply centralized redaction before any logging; add retention guidance and an explicit local reveal/export workflow. Keep response storage configurable while recording the reproducibility tradeoff.

### High: target content crosses the attacker-model boundary silently

Full target responses are sent to `check_sensitive_data()`, and full accumulated conversations are included in follow-up planning. With the OpenAI provider this leaves the machine; with a remote Ollama-compatible URL it may also leave the machine. The result does not record this disclosure.

**Required control:** Default `target_data_sharing` to `none`. Require explicit `redacted` or `full` policy for adaptive planning or LLM judging, show the provider/endpoint classification, minimize context, strip target authentication material, cap size, and record disclosure policy plus payload digests/sizes. Deterministic oracles and static attacks must work without any model provider.

### High: a removed cookie remains in Git history

Repository history contains one non-empty `http.cookies` mapping before commit `722a889`; the current working tree is empty. Commit messages also refer to removing a sensitive cookie. The value was intentionally not printed or copied into these documents.

**Required control:** Treat the historical value as compromised, rotate/revoke it if that has not already happened, review repository forks/logs, and decide with maintainers whether coordinated history rewriting is appropriate. Add automated secret scanning and synthetic fixtures. History rewriting is a separate maintainer operation and should not be performed as part of the architecture refactor without approval.

### High: browser security is weakened unconditionally

New Selenium sessions add `--ignore-certificate-errors`, `--ignore-ssl-errors`, `--allow-running-insecure-content`, and `--no-sandbox`, and the code bypasses certificate warning pages. This makes interception and browser compromise more plausible when testing an untrusted target. TLS bypass is not scoped to an explicit scenario choice.

**Required control:** Playwright contexts verify HTTPS by default. `ignore_https_errors` requires explicit configuration and a result warning. Support a CA bundle/trusted test proxy where practical. Do not disable the Chromium sandbox by default; if a particular container requires it, make it a clearly labeled advanced runtime opt-in.

### High: attached Chrome exposes authenticated state

Legacy existing-Chrome mode attaches to `localhost:<remote_debugging_port>` and reuses whatever authenticated page/profile the user opened. The tool does not isolate origins, distinguish browser ownership on close, restrict trace/network capture, or manage the profile. Chrome remote debugging is a powerful local control interface.

**Required control:** Prefer Playwright storage state loaded into an isolated context. Mark storage state as a secret artifact, require a dedicated test account/profile, and never copy it into results. Manual login should be an explicit headed workflow. Legacy attach mode needs deprecation warnings and must not close a browser it does not own. Documentation should advise binding debugging to loopback and terminating it after use.

### Medium/high: artifact stores and caches have no sensitivity policy

OpenAI cache responses, prompt-chain data, results, and future screenshots/traces can contain protected content. `.gitignore` reduces accidental commits but does not restrict local readers, backups, indexing, support bundles, or cloud sync. Cache entries have no retention or real timestamp (`cached_at` currently stores a serialized empty object).

**Required control:** Route every artifact through one restricted `ArtifactStore` with classification (`metadata`, `transcript`, `secret-bearing`), generated path, safe permissions, size cap, SHA-256, atomic write, and optional retention deletion command. Disable cross-run provider caching by default for target-derived inputs, or partition it per engagement with an explicit policy. Never cache resolved credentials.

### Medium: secret loading is inconsistent and over-broad

Environment substitution occurs recursively in all YAML strings, and resolved values remain in the monolithic in-memory config. `.env` is loaded from process cwd. `python-dotenv` normally preserves existing environment values, while the manual fallback overwrites them. OpenAI key validation prints a ten-character prefix on format failure. README suggests hard-coding a key as an alternative.

**Required control:** Use explicit `${env:NAME}`/`${file:path}` secret references, resolve at component construction, keep redacted wrappers in configuration snapshots, and never print prefixes. Resolve `.env` behavior relative to a documented project/scenario root and never override an already-set environment variable unless explicitly configured. Remove hard-coded secret guidance and provide an example secrets file that is clearly ignored.

### Medium: proxy behavior can expose credentials or traffic

Proxy credentials may be embedded in URLs and reconstructed into request proxy strings. Error messages from Requests/proxy layers may include endpoint details. Selenium accepts authenticated proxy configuration but warns that it does not support it. The scope name `api` means attacker-model traffic, even though users may reasonably read it as target API traffic.

**Required control:** Make proxy settings component-specific (`target.proxy`, `planner.proxy`), source credentials separately, always redact userinfo, and validate support before execution. Never silently continue without a configured proxy; fail closed when proxy use is required. Provide explicit CA trust for intercepting proxies rather than disabling TLS globally.

### Medium: untrusted target/model text can manipulate terminals and reports

Target response and model output are printed verbatim. A hostile string can contain ANSI escapes, carriage returns, bidirectional-control characters, or log prefixes. Current HTML is read as `.text`, and model output is sent by Selenium keystrokes; there is no direct shell/`eval` sink. Future HTML reports or trace annotations could introduce stored injection if content is not escaped.

**Required control:** Escape/control-filter terminal output, use structured logs with untrusted fields, HTML-escape every report value, and set a restrictive Content Security Policy if HTML reporting is added. Preserve the rule that scenarios and model outputs cannot supply shell commands, Python, or arbitrary browser JavaScript.

### Medium: session contamination weakens evidence

All legacy tests reuse one browser/page. A disclosure may depend on an earlier test or target user state, and repetitions would not be independent. There is no response correlation, so a previous assistant message can be mistaken for the new response.

**Required control:** Default to a new adapter session per repetition, record session-reset policy, correlate each response with the current request/DOM change, and keep target conversation IDs inside the adapter session. Shared-session scenarios must opt in and be labeled non-independent.

### Medium: resource, rate, and error controls are incomplete

Maximum turns and prompt lengths exist, but there is no total duration, target response byte limit, model token/cost budget, conservative retry strategy, target rate-limit state, artifact quota, or cancellation-safe partial result. Send/extraction failures are reported as normal completion.

**Required control:** Enforce per-message/total bytes, turns, wall time, repeated payload/response limits, provider tokens/cost, retries, rate, and artifact sizes. Default concurrency to one. Preserve typed timeouts/rate limits/errors and make them affect final status rather than “not detected.” Respect `Retry-After`; do not retry side-effecting POSTs without explicit policy.

### Low/medium: current security claims create false confidence

README describes URL validation as SSRF prevention and selector substring checks as XSS prevention. Neither is a meaningful control in this threat model. It also frames automatic certificate bypass as troubleshooting support.

**Required control:** Replace claims with precise behavior and limitations. Validation prevents malformed configuration, not malicious authorized configuration. Target pages remain hostile, and TLS/proxy choices remain explicit operator responsibilities.

### Medium: the default configuration contacts a real public target

The checked-in `config.yaml` points at a non-loopback public prompt-injection challenge and enables existing-Chrome attachment by default. Running the basic command has no explicit authorization preflight and is not a credential-free local demo.

**Required control:** Make the shipped default scenario the loopback synthetic demo. Require a warning and acknowledgment before any non-local target, and never generate example configurations containing real cookies, tokens, or third-party targets that could be mistaken for authorized scope.

## Controls for the target architecture

### Secrets and configuration

- Parse YAML with a safe loader and reject unknown keys/types.
- Limit file references to regular files and safe, explicit roots; reject traversal in output/artifact paths.
- Never serialize resolved secret values. Fingerprint sanitized configuration only.
- Maintain a default header/field denylist: authorization, proxy authorization, cookies, API keys/tokens, storage state, and scenario additions.
- Do not place real credentials in examples, tests, generated configs, screenshots, or error fixtures.
- Ensure secret values are excluded from planner context even in `full` target-data mode.

### Network and TLS

- Verify target and provider TLS by default; support CA bundles.
- Require explicit `allow_insecure`/`ignore_https_errors`, warn before execution, and record it.
- Use component-specific proxies and fail rather than bypass a required proxy.
- Keep callback listeners on loopback by default, randomize the port/token, validate correlation, and shut them down after the run.
- Allow non-local/private targets because that is necessary for authorized assessments, but warn and require acknowledgment. Do not perform surprise DNS/connectivity checks during `validate`.

### Browser isolation

- Use a new Playwright context per target session/repetition by default.
- Use a dedicated test account and storage-state file; no everyday browsing profile.
- Disable downloads, clipboard, geolocation, camera/microphone, and other permissions unless explicitly required.
- Restrict browser engines/channels to tested choices, preserve the sandbox, and clean up owned contexts/processes on cancellation.
- Treat traces, screenshots, videos, HAR, network bodies, and storage state as sensitive. Capture the minimum needed.
- Declarative flows use allowlisted actions; there is no arbitrary `evaluate` action in version 1.

### External model providers

- Show whether a provider URL is local/remote as an informational classification, not a guarantee.
- Default to no target-data sharing; adaptive/judge features require explicit policy.
- Bound and redact transcript context before transmission; include only goal, safe target description, recent turns/evidence summaries, and remaining budget.
- Require structured output and store only the brief reasoning summary, never hidden chain-of-thought.
- Track reported token use/cost and enforce configured budgets.
- Do not make provider-side caching/retention promises; document that operators must evaluate provider terms for their data.

### Evidence integrity and privacy

- Evaluate exact/fragment/regex/protected-document/callback evidence locally before any LLM judge.
- Store oracle type, turn, offsets when safe, match digest, redacted preview, and artifact hash.
- Distinguish `confirmed`, `likely`, `inconclusive`, `not_detected`, and `error`; never treat an oracle failure as “no finding.”
- Write result and artifact files atomically and restrict permissions. Preserve each repetition separately.
- Avoid including protected values in summary JSON. A deterministic match can be proven by an engagement-local digest plus restricted raw transcript.
- Sanitize target-controlled filenames, headers, content types, and suggested artifact labels.

### Safe payload and execution defaults

- Ship non-destructive prompt-injection payloads focused on synthetic disclosure.
- Do not include instructions to steal real browser credentials, alter data, execute system commands, persist, contact public exfiltration services, or evade authorization controls unrelated to information disclosure.
- Default concurrency to one, add a minimum message delay, and respect target rate limits.
- Stop on confirmed evidence, configured bounds, target unavailability, or repeated no-progress behavior.
- Never use a database/service when restricted per-run files are sufficient.

## Logging and error-handling policy

Use structured events internally and render a safe subset. Each error has:

- a stable type/code and component;
- a safe operator message with no response body, secret, cookie, URL userinfo, or raw header;
- private diagnostic detail stored only when explicitly enabled in the restricted run directory;
- timestamps, retry/timeout metadata, and a causal chain without secret-bearing exception strings.

Verbose mode is more diagnostic, not unredacted. A separate explicit `show-evidence`/local file access workflow can reveal target content. Terminal rendering strips ANSI/control characters or escapes them visibly.

## Artifact layout and permissions

Recommended layout:

```text
results/<timestamp>-<target>-<experiment-id>/       0700
├── summary.json                                    0600
├── run-001/                                        0700
│   ├── result.json                                 0600
│   ├── conversation.json                           0600
│   ├── raw/                                        0700
│   ├── trace.zip                                   0600
│   ├── network.har                                 0600
│   └── screenshots/                                0700
└── run-002/
```

Permissions are best effort on non-POSIX systems and the limitation is documented. Never follow an existing symlink when creating artifacts. Resolve/validate the result root and use generated child names. The runner should refuse to write into a world-writable existing directory unless the user explicitly selects it and safe subdirectory creation succeeds.

## Security testing of the tool

The default test suite should include:

- secret redaction across logs, errors, result snapshots, proxy URLs, headers, and nested configuration;
- path traversal/symlink artifact tests;
- hostile ANSI/HTML/bidirectional output tests;
- TLS verify/insecure opt-in tests and proxy fail-closed behavior;
- response, SSE, trace/HAR, token/cost, callback, and runtime limits;
- cancellation and partial-result integrity;
- separation of browser sessions/cookie jars across repetitions;
- proof that `validate` makes no network/model/browser calls;
- proof that `target_data_sharing: none` makes no provider call containing target data;
- checked-in secret scanning and dependency review.

Do not include paid external APIs or public targets in default CI. Use a deterministic fake planner and the loopback vulnerable/safe demo.

## Incident response guidance

If a run captures real confidential information:

1. stop further repetitions unless continued collection is authorized and necessary;
2. keep console sharing/screenshots off and restrict the result directory;
3. record the run ID and artifact hashes without copying the protected value into tickets/chat;
4. follow the target owner's disclosure/incident process;
5. rotate exposed credentials/tokens where applicable;
6. securely remove artifacts according to engagement retention policy, accounting for backups/sync;
7. if content was sent to an external planner/judge, record the provider and disclosure policy for the incident owner.

The tool should support this workflow with clear stop reasons, minimal summaries, and an explicit retention/deletion command later, but it must not automatically delete reproducibility evidence without user authorization.
