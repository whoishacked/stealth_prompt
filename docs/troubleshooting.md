# Troubleshooting

## The extension cannot reach Local Core

Confirm `stealth-prompt serve` is still running. If it uses `--port`, enter the same port
in the panel. Restarting Core creates a new pairing code and invalidates the old token.

## Pairing is rejected

A code expires after 15 minutes, works once, and is invalid after five wrong attempts.
Restart Core for a fresh code.

## The current tab cannot be accessed

Activate the target tab and click the Stealth Prompt toolbar icon. That user gesture opens
the Side Panel, records the target tab, and grants temporary `activeTab` access. Then choose
**Use current tab** or select an element again.

Chrome blocks extension injection into browser-internal pages, the Chrome Web Store, and
some PDF or extension pages. Open an ordinary HTTP(S) target page.

## Element selection appears to do nothing

Keep the target tab active, press the relevant **Pick manually** action, then click the
element in the page. Approve Chrome's per-site permission request if it appears. Controls
inside inaccessible iframes are not supported in this release.

## A locator is missing or ambiguous

The document changed or the locator resolves to more than one element. Re-run detection or
pick that role manually. The run pauses because sending through a guessed control is unsafe.

## Capture times out

The Test workspace counts down 15 seconds while watching for a stable reply. After a
timeout, **Check page again** watches the original post-send snapshot for another 15
seconds without sending the payload again. **Use response & continue** is available as
a manual fallback, including during Auto; **Re-detect response** performs a read-only
DOM scan and opens the exact Setup step that can fix the selector. For canvas,
virtualized, or cross-frame output, select **Paste response** before starting an Assist
or Guided run.

If the turn completes but the captured text is wrong, use **Paste bot response** beside
**Generate payload**. If the response binding includes the input or Send control, select
one assistant reply instead of the whole chat page; Stealth Prompt rejects that broad
binding because input changes can otherwise be mistaken for bot output.

## Everything remains Potential

This is expected without deterministic evidence. Configure a scorer such as
`stealth-prompt serve --expect-regex '<synthetic-canary>'`, or verify the finding manually.

## CLI generation feels slow

The panel waits for a complete schema-valid planning result, while a terminal shows the
first streamed token immediately. The proposal timing separates provider latency from page
capture time. Choose a faster model if the provider operation dominates.

## Auto stopped after a provider or JSON error

Dismiss the local error and use the recovery or continuation action shown with the stopped
run. Re-check the interaction when requested. A transient provider failure should not require
discarding the assessment; if no recovery action appears, include the timeline and browser
version in a [bug report](https://github.com/whoishacked/stealth_prompt/issues).
