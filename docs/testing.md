# Run a test

Stealth Prompt tests one interaction that you explicitly bind. It does not crawl the
site or select a scope on your behalf.

## Configure the assessment

1. Choose a provider and model.
2. Choose a behavior and security objective.
3. Choose how replies are captured.
4. Set data sharing and automatic-run limits.

### Behaviors

| Mode | What happens |
| --- | --- |
| Payload only | Generates text and never mutates the page |
| Assist | Generates on request; every send needs approval |
| Guided | Prepares the next proposal after a reply; every send needs approval |
| Auto | Runs an adaptive loop within the limits you explicitly authorize |

Auto defaults to 20 turns with unlimited time. Turns can be limited to 1–100 or set to
Unlimited. Unlimited turns require **Pause** or **Stop** when a potential finding appears,
so an unattended run cannot continue forever without a finding policy.

## Bind the interaction

Open the target page and click the extension toolbar icon. Select **Use current tab**,
then start with **Detect elements**. The detector proposes each role independently:

- **Input** — the editable chat field;
- **Send control** — the control associated with that input;
- **Response container** — the assistant output to capture.

Review each suggestion and save the interaction. Use **Highlight** to locate a match or
**Pick manually** when detection is wrong. A manual pick is already an operator choice
and does not require a second acceptance step.

The extension adds private markers to the selected DOM elements for stable execution
inside the current document. It does not change the page's application identifiers.
After navigation or a re-render, validation either resolves the reviewed locator again
or pauses instead of acting on an ambiguous element.

### When response capture is unreliable

Choose **Paste response** for canvas output, virtualized conversations, inaccessible
frames, or a response container that cannot be identified reliably. Only input and send
control are required; paste the latest assistant reply before generating the next turn.

## Verify before sending

**Fill harmless test draft** writes a benign draft to the selected input and never
presses Send. Use it to verify the binding without contacting the target agent.

## Findings during Auto

Choose one policy before starting:

- **Pause** for operator verification;
- **Stop** and save the finding;
- **Continue** while recording the signal in the final report.

Potential does not mean confirmed. Confirmation requires a deterministic scorer or an
explicit operator decision. You can confirm and continue when you want to preserve the
evidence and explore the chain further.

## Continue a useful line of investigation

The planner receives the bounded conversation history, current objective, tactic,
hypothesis, prior signals, and attempted approaches. It should advance or change the
test strategy, not merely answer the target's last sentence. At a turn limit, use the
continuation action to grant another bounded block without discarding the session.

For the complete behavior, persistence, protocol, and binding reference, see the
[browser extension guide](extension.md).
