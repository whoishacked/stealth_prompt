# Privacy

Stealth Prompt is local-first. The project does not require a Stealth Prompt cloud
account and does not operate a service that receives target page content.

## Data processed by the extension

The extension may process the origin, text input, selected response content and DOM
locators for the exact interaction chosen by the operator. It does not intentionally
read cookies, passwords, target access tokens, `localStorage` or `sessionStorage`.

Extension settings, pairing information and locators are stored in Chrome's local
extension storage. Manually pasted target responses are not persisted there.

Direct API assessments are stored separately in the extension's browser-local
IndexedDB so they remain visible in Reports. A report can contain the selected target
responses, payloads and evaluations, but never the provider API key. The library keeps
at most 50 reports; each can be downloaded or deleted in the extension.

In optional Direct API mode, an OpenAI or Anthropic key is held only in the open Side
Panel's JavaScript memory and passed to the extension service worker for the selected
provider request. It is never written to Chrome storage, bindings, timelines, or
exports, and closing the panel clears it. The key still exists in the browser process;
use Core mode if that exposure is unacceptable.

## Local Core and providers

The local Core stores session evidence in the configured artifacts directory. Files
may contain sensitive target output and are readable by the local operating-system
user. The operator controls retention and deletion.

### Scenario files

A scenario (`scenario.json`) is written next to, but separately from, the session
evidence. It records only how an assessment was configured: objective, provider
kind and requested model, mode and limits, sharing policy, target origin, the
reviewed interaction binding, and the deterministic scorer configuration.

A scenario deliberately contains **no** captured target responses, transcripts,
verdicts, session identifiers, credentials, API keys, tokens, cookies, storage or
headers. The parser rejects a credential- or capture-shaped field on import
rather than dropping it silently. This separation is what lets a scenario be
shared with a colleague or committed to a repository when the evidence itself
cannot be.

Importing a scenario grants no authority: it never restores automatic-send
authorization, and replay still requires current host permission and a fresh
validation of the binding against the live page.

### Sharing policy

The selected sharing policy determines whether target responses reach an AI provider:

- `none`: target responses are not sent to the provider;
- `redacted`: credential-shaped values are removed before submission;
- `full`: the selected response is sent verbatim.

Claude, Codex and OpenAI may be external services depending on the operator's own
configuration and provider agreement. Ollama is restricted to loopback. Stealth
Prompt does not change provider retention policies.

Direct API mode contacts only the fixed `https://api.openai.com` or
`https://api.anthropic.com` origin selected by the operator. Chrome requests that host
permission when the operator enables the connection.

## Permissions

Host permission is requested for the selected origin only. The extension does not
request blanket access to every website. See `docs/extension.md` for the purpose of
each Chrome permission and the threat model.

## Deletion

Direct API reports can be deleted from Reports. Uninstalling the extension removes its
Chrome-managed local storage and IndexedDB. Core session evidence must be deleted
separately from the configured artifacts directory.
