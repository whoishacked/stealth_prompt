# Stealth Prompt

**A local-first AI security workbench for real browser interactions.**

Stealth Prompt helps an authorized tester investigate one exact AI chat or agent
workflow inside the browser session they already use. It observes the selected
interaction, proposes an evidence-driven test message, lets the operator review the
action, captures the reply, and recommends the next step.

It is closer to a focused Repeater for AI-agent interfaces than to a broad automatic
scanner.

> Use Stealth Prompt only on systems you own or are explicitly authorized to assess.

## Why Stealth Prompt

- **Real authenticated browser context.** Test the UI and account state that matter,
  without copying cookies into an automation profile.
- **Exact scope.** Detect or pick the input, send control and response container;
  review every suggestion before saving. The model cannot invent a selector, URL,
  command or browser operation.
- **Progressive autonomy.** Generate only, approve every send, prepare guided
  follow-ups, or explicitly authorize a bounded automatic loop.
- **Bring your provider.** Claude CLI, Codex CLI, local Ollama and provider APIs are
  supported through the local Core. OpenAI and Anthropic can also run directly from
  the extension when zero local setup matters more than credential isolation.
- **Evidence, not vibes.** A model assessment can identify a potential issue, but a
  confirmed finding requires a deterministic check or explicit operator verification.
- **Local-first privacy.** Stealth Prompt has no required product account. Core mode
  keeps credentials outside Chrome; optional direct mode keeps a supplied key only in
  the open panel's memory. Target replies follow an explicit none/redacted/full policy.

## The workflow

```text
Observe → hypothesize → generate → review → send → capture → verify → report
```

The Side Panel is organised as four workspaces — **Setup**, **Test**, **Review**
and **Reports** — with bounds and scenario import/export in a **Settings**
drawer. Only one workspace renders at a time, and the panel moves between them
as the assessment moves: starting a run opens Test, a potential finding follows
the selected review/stop/continue policy, and a terminal result opens the
terminal summary in Reports. A reload reopens the workspace the assessment is
actually in.

The current objective catalogue covers direct and indirect prompt injection,
instruction and sensitive-data disclosure, role confusion, goal hijacking, RAG
manipulation, memory poisoning, tool misuse, excessive agency, approval bypass and
unsafe output handling.

## Quick start

Install [Stealth Prompt from the Chrome Web Store](https://chromewebstore.google.com/detail/stealth-prompt/genafpggpdjagohhbngddncbanhpcdpm),
then pin it to the Chrome toolbar. Chrome 116 or newer is required.

For the recommended local Core path, Python 3.10 or newer is also required:

```bash
# Install the local Core from this checkout.
python -m pip install .

# Start the local Core.
stealth-prompt serve
```

Then:

1. Open Stealth Prompt from the toolbar on the target tab.
2. Enter the one-time pairing code printed by `stealth-prompt serve`.
3. Start with the **Fake** provider and the bundled local demo.

Alternatively, choose **Direct API** in the panel, enter an OpenAI or Anthropic key,
and load the models without installing or pairing the Core. The key is not saved, but
it exists in the browser process; use a restricted project key with a spend limit.

### The guided demo

For a first success, one command starts the local demo target *and* the Core
together, with the demo's synthetic canary already
configured as the deterministic check, and prints the pairing code, the browser
steps and what to do when something fails:

```bash
stealth-prompt demo
```

For a stateful adaptive test, add `?mode=advanced` to the printed demo URL. It
requires two different turns before the synthetic canary can be disclosed;
`?mode=safe` is the negative control.

You never type an attack string — the first payload is generated from the
objective you chose. The run ends `confirmed` because the demo disclosed the
exact canary the Core was told to look for, not because a model judged it so.
The machine portion of that path measures about 5.4 seconds; the rest is your
reading and clicking.

The Side Panel walks through connection, target selection, configuration, element
binding and the first generated payload. A successful export creates both structured
`session.json` evidence and a self-contained `report.html` suitable for review,
plus a `scenario.json` that replays the setup without carrying any evidence.

In Core mode the Reports workspace lists previously exported runs and opens
their results in the extension, with separate HTML and JSON downloads. Direct
API mode keeps up to 50 reports in this Chrome profile through IndexedDB and
also supports explicit JSON downloads. These local reports may contain target
responses and can be viewed or deleted from Reports.

Read the [documentation](https://whoishacked.com/stealth_prompt/) for installation,
provider setup, modes, reports, permissions, verdicts and troubleshooting. The Markdown
source remains in [`docs/`](docs/index.md).

## Architecture

```text
Target page ← allowlisted operations ← Chrome extension
                                      ├─ paired WebSocket → Local Core → CLI/API providers
                                      │                          └─ scorers + evidence
                                      └─ optional direct HTTPS → OpenAI / Anthropic
```

The extension owns browser observation and execution. In the recommended Core path,
the Core owns credentials, CLI processes, deterministic scoring and artifacts. Direct
mode intentionally trades those Core-only capabilities for a no-install API path.

This separation is intentional: a hostile target page or model response must not be
able to broaden the selected scope.

## Modes

| Mode | Generates | Touches the page | Approval |
|---|---|---|---|
| Payload only | Yes | Never | Copy manually |
| Assist | On request | On send | Every send |
| Guided | Next proposal automatically | On send | Every send |
| Auto | Adaptive loop with operator-selected limits | Yes | One explicit run authorization |

Auto can pause on a potential finding, stop and save it, or continue while recording
all signals. Reaching a configured turn or duration limit saves the report automatically;
authorization is never persisted across a panel reload. The default is 20 turns with
unlimited time. Turns support 1–100 sends or Unlimited; unlimited turns require Pause
or Stop when a potential finding appears.

## Data and trust

The extension receives host access only for origins the operator approves at runtime;
it has no blanket host access at install. It does not request Chrome permissions for
cookies, web requests, or debugger access. The shipped code contains no remote
executable code.

Read [PRIVACY.md](PRIVACY.md), [SECURITY.md](SECURITY.md) and the detailed
[threat model](https://whoishacked.com/stealth_prompt/extension/#threat-model) before
using target data with an external provider.

## Contributing

Development setup and required checks are in [CONTRIBUTING.md](CONTRIBUTING.md).
Current priorities are in the [product roadmap](docs/product-roadmap.md), and shipped
changes are recorded in [CHANGELOG.md](CHANGELOG.md).

## Current status

Version 0.2 is under active development. The browser extension and Local Core are the
primary product path.

The previous automation-browser Workbench and original Selenium runner remain for
compatibility but are deprecated. New product development targets the extension and
Local Core.

## License

MIT. See [LICENSE.txt](LICENSE.txt).
