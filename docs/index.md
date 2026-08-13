---
hide:
  - toc
---

# Test AI agents in the browser you already use

Stealth Prompt is a browser-native assistant for authorized AI security testing. Pick
one chat interaction, generate an evidence-driven test message, inspect the reply, and
continue the same line of investigation without moving cookies or recreating the UI in
an automation script.

[Install from Chrome Web Store](https://chromewebstore.google.com/detail/stealth-prompt/genafpggpdjagohhbngddncbanhpcdpm){ .md-button .md-button--primary }
[Run the guided demo](getting-started.md){ .md-button }

!!! warning "Authorized targets only"
    Use Stealth Prompt only on systems you own or are explicitly authorized to assess.

## Why it exists

<div class="grid cards" markdown>

-   **Real browser context**

    Test the authenticated UI and account state already open in Chrome.

-   **Exact interaction scope**

    Detect or select the input, send control, and response container. The model cannot
    invent a browser operation or silently broaden the target.

-   **Progressive autonomy**

    Generate only, approve each send, prepare guided follow-ups, or authorize a bounded
    automatic run.

-   **Evidence-backed results**

    Model judgement can raise a potential finding. Confirmation requires a deterministic
    check or an explicit operator decision.

</div>

## Two ways to connect AI

| Path | Best for | Providers | Evidence |
| --- | --- | --- | --- |
| Local Core | CLI access, credential isolation, deterministic scorers, durable artifacts | Claude CLI, Codex CLI, Ollama, OpenAI API, Fake | JSON and self-contained HTML on disk |
| Direct API | Fastest setup with no local service | OpenAI API, Anthropic API | Up to 50 browser-local reports and JSON download |

[Compare connection modes](connections.md)

## The assessment loop

```text
Observe → hypothesize → generate → review → send → capture → verify → report
```

Stealth Prompt is deliberately closer to a focused Repeater for an AI-agent interface
than to a broad automatic scanner. The operator chooses the page, interaction, objective,
data-sharing policy, and autonomy limits.

## Start here

1. [Install the extension and run the local demo](getting-started.md).
2. [Choose Local Core or Direct API](connections.md).
3. [Bind the target interaction and run a test](testing.md).
4. [Review and export the evidence](reports.md).
