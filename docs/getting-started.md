# Get started

This path proves the complete browser-to-Core flow against a local synthetic target.
It does not contact an external AI provider and does not require an API key.

## Requirements

- Chrome 116 or newer
- Python 3.10 or newer for Local Core
- the [Stealth Prompt extension](https://chromewebstore.google.com/detail/stealth-prompt/genafpggpdjagohhbngddncbanhpcdpm)

Pin the extension to the Chrome toolbar after installation.

## Install the Local Core

Clone the repository and install the Python package:

```bash
git clone https://github.com/whoishacked/stealth_prompt.git
cd stealth_prompt
python -m pip install .
```

## Run the guided demo

```bash
stealth-prompt demo
```

The command starts an intentionally vulnerable target and the loopback-only Core. It
prints the target URL, Core port, one-time pairing code, and the exact browser steps.

1. Open the printed target URL in Chrome.
2. Click the Stealth Prompt toolbar icon.
3. Enter the pairing code and choose **Use current tab**.
4. Choose **Detect elements**, review the three suggested roles, and save the interaction.
5. Keep the **Fake** provider and start the test.

The first payload is generated automatically. The demo ends with a confirmed result
because the target returns a synthetic canary matched by a deterministic scorer.

!!! tip "Try an adaptive chain"
    Add `?mode=advanced` to the demo URL. The canary requires two different turns.
    Use `?mode=safe` as the negative control.

## Next steps

- [Connect Claude CLI, Codex CLI, Ollama, OpenAI, or Anthropic](connections.md).
- [Choose an objective, mode, and response trigger](testing.md).
- [Understand verdicts and exported evidence](reports.md).
