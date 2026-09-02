# Choose a connection

The first decision is where provider access and assessment state should live.

## Decision guide

| Need | Choose |
| --- | --- |
| Claude CLI or Codex CLI | Local Core |
| Local Ollama | Local Core |
| Provider key outside the browser | Local Core |
| Deterministic scorers and self-contained HTML evidence | Local Core |
| No local installation or service | Direct API |
| OpenAI or Anthropic with a session-only key | Direct API |

## Local Core

Start the Core in a terminal:

```bash
stealth-prompt serve
```

It binds to loopback, prints a one-time pairing code, and never opens a browser. Enter
the code in **Setup → Local Core**. The extension exchanges it for a token bound to the
extension origin. Restarting the Core invalidates the token.

The Core discovers available providers and models:

- **Fake** is deterministic, offline, and intended for the demo and workflow checks.
- **Claude CLI** uses the installed and authenticated `claude` executable.
- **Codex CLI** uses the installed and authenticated `codex` executable.
- **Ollama** uses a loopback endpoint and refuses a remote URL.
- **OpenAI API** reads `STEALTH_PROMPT_OPENAI_API_KEY`, then `OPENAI_API_KEY`.

Run `stealth-prompt doctor` when a CLI provider is missing or model discovery fails.
Use `stealth-prompt serve --port <port>` for a non-default port and enter the same port
in the extension. Core also owns the private, revisioned strategy library. It uses
`.stealth-prompt/strategies.sqlite3` by default; pass `--strategy-db <file>` to keep it
elsewhere. Strategy snapshots contain reusable abstractions, not captured replies or
credentials. Before each planning turn, Core filters active strategies by objective,
scope, mode, observed surface/capabilities, response pattern, and exhausted moves. It
then exposes at most three bounded strategy summaries to the planner. Target and project
scope keys are SHA-256 identifiers rather than stored origins.

Core can also learn from a completed report, but only when the operator enabled
**Offer this run for reviewed learning** before that run. Reports shows eligibility and,
on request, generates a sanitized preview using the source report's provider and model.
Accept, Edit, and Keep target-specific are the only actions that mutate the local
library; Reject records only the decision. Reopening an accepted report is idempotent.
Reviewed libraries can also be evaluated against a frozen local benchmark, promoted
through fixed quality gates, and exchanged as explicitly trusted signed files. See
[Evaluate and share strategies](evaluation-and-sharing.md).

## Direct API

Choose **Direct API**, then OpenAI or Anthropic. Enter a restricted project key and
select **Use key & load models**. Chrome requests access only to the selected provider's
fixed API origin.

The key is held in Side Panel and service-worker memory only while needed. It is not
written to `chrome.storage`, bindings, reports, or exports. Closing the panel clears it.

!!! warning "Convenience boundary"
    The key still exists in the Chrome process. Use a restricted key with a spend limit,
    and prefer Local Core if browser-process exposure is unacceptable.

Direct API supports Payload only, Assist, Guided, and bounded Auto. It does not provide
CLI providers, Core scenarios, deterministic scorers, the Core's HTML artifact, or a
mutable private strategy library. It does use the shipped read-only strategy catalogue,
with the same bounded selection and report attribution.

Direct reports can be reviewed and exported in the extension, but they are not learning
inputs and never mutate a paired Core. Reports shows a quiet link to the Core setup when
you want the private, revisioned library.

## Data sharing

The selected policy controls whether captured target responses reach the provider:

| Policy | Provider receives |
| --- | --- |
| `none` | No target response |
| `redacted` | Response with credential-shaped values removed |
| `full` | Selected response verbatim |

Claude, Codex, OpenAI, and Anthropic may be external services under your own provider
agreement. Ollama is restricted to loopback. Stealth Prompt does not change provider
retention policies.
