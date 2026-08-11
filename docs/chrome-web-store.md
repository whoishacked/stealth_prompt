# Chrome Web Store release brief

Published listing: [Stealth Prompt — Chrome Web Store](https://chromewebstore.google.com/detail/stealth-prompt/genafpggpdjagohhbngddncbanhpcdpm)

## Listing copy

**Name:** Stealth Prompt — AI Security Workbench

**Short description:** Test an exact AI chat interaction in your authenticated
browser with local-first, evidence-driven security workflows.

**Single purpose:** Stealth Prompt helps an authorized security tester generate,
review, send, capture and document targeted AI security tests against the exact chat
interaction selected in the current browser.

The listing must not call the product an automatic vulnerability scanner or imply
complete OWASP coverage.

## Permission justification

The extension does not request cookies, debugger, webRequest, tabs, or `<all_urls>`.

The blocks below are written to be pasted verbatim into the corresponding fields
of the Privacy practices tab. Each one states what the permission does and why a
narrower permission would not work, which is what reviewers look for.

### `sidePanel`

> The product interface is a Side Panel so it can stay open beside the page under
> test while the operator reviews each proposed test message and the reply it
> produced. An assessment is multi-turn and stateful: a popup closes on the next
> click and would lose the run, and a separate tab would hide the page being
> examined.

### `storage`

> Keeps non-secret local state so an assessment survives closing the panel or
> reloading it: the selected objective and mode, the run limits, the reviewed
> element locators for the chosen interaction, and the current turn count.
> Provider API keys are never written to storage.

### `scripting`

> Injects the extension's own bundled content script into the single tab the
> operator authorized, so it can fill the selected input, activate the selected
> send control, and read the selected response container. Only the fixed script
> shipped inside the package is injected. No remote, downloaded, or generated
> code is ever executed, and the set of page operations is a closed allowlist.

### `activeTab`

> Identifies which tab the operator invoked the extension from, so the assessment
> binds to that one tab instead of to any page the browser later loads.

### Optional host permissions (`http://*/*`, `https://*/*`)

> These are optional host permissions, requested at runtime and never granted at
> install. The operator opens the tab they are authorized to test and presses
> "Use current tab"; Chrome's own consent prompt then grants access to that one
> origin, and the extension operates on no other origin.
>
> The pattern is broad because the tool is used by security testers against
> systems they own or are contracted to assess. That target is different for
> every user and every engagement, so it cannot be enumerated in the manifest
> ahead of time. Breadth here is the set of origins a user may *choose from*, not
> the set the extension actually receives — that is always exactly one.

### Remote code

> No remote code. All logic is bundled from source in the repository with esbuild
> and ships inside the package. Extension pages run under
> `script-src 'self'; object-src 'none'; base-uri 'none'`, and the build uses no
> CDN, no `eval`, and no `new Function`.

## User-data disclosure

Tick these categories on the Privacy practices tab, with the stated reason:

| Category | Collected | Why |
|---|---|---|
| Website content | Yes | The selected input text, the selected reply, the target origin, and DOM locators for the one reviewed interaction. |
| Authentication information | Yes | Only in optional Direct API mode, and only the provider key the user types. It is held in the open panel's memory, sent solely to the API origin the user picked, never written to storage or exports, and never sent to the developer. |
| Personally identifiable information | No | |
| Financial and payment information | No | |
| Health information | No | |
| Personal communications | No | |
| Location | No | |
| Web history | No | |
| User activity | No | No clickstream, keystroke, or mouse monitoring. Only the operator's own test messages inside the panel. |

Over-disclose rather than under-disclose: the Direct API key entry is why
"Authentication information" is ticked even though the developer never receives
it, and the explanation field is what keeps that from reading as a red flag.

The three required certifications can all be affirmed truthfully:

- data is not sold or transferred to third parties outside the approved use cases
  — the only third party is the AI provider the user selected and configured;
- data is not used or transferred for any purpose unrelated to the single purpose;
- data is not used or transferred to determine creditworthiness or for lending.

The listing must also explain that processing happens in a local Core, that
Stealth Prompt operates no required cloud service, and that provider-side
retention is governed by the user's own provider account and agreement.

## Required visual assets

- 128×128 product icon (already present in `extension/icons`).
- 440×280 small promotional tile: `docs/store/promo-tile-440x280.png`, generated
  by the same tool from the shipped product icon.
- Four 1280×800 screenshots in `docs/store/`, regenerated with:

  ```bash
  python tools/store_screenshots.py
  ```

  The tool loads the shipped `extension/dist` in Chromium beside a real Core and
  the loopback demo target, drives the Side Panel to each state, and composites
  the panel next to the target page. Every frame is the real product against
  synthetic data — no mockups, no real target, nothing external contacted.

  1. `01-select-interaction` — discovery suggestions awaiting per-role approval;
  2. `02-provider-privacy-mode` — provider, model and data-sharing policy;
  3. `03-review-before-send` — hypothesis and editable payload before approval;
  4. `04-evidence-and-report` — confirmed verdict with its evidence.

- A 45–60 second workflow video with the local demo and no real target data.

## Release checklist

- [ ] Listing name matches `manifest.json` — the store shows the manifest name,
      so "Stealth Prompt" and "Stealth Prompt — AI Security Workbench" must be
      reconciled before upload.
- [ ] Public HTTPS privacy-policy URL.
- [ ] Support URL and monitored security-reporting path.
- [ ] Store disclosures match `PRIVACY.md` and runtime behaviour.
- [ ] Version matches Python package, extension manifest and changelog.
- [ ] Release ZIP contains `manifest.json` at its root.
- [ ] No source map, remote code, development host permission or test fixture ships.
- [ ] Manual review of the external-provider warning, pairing and permission denial.
- [ ] Fresh-profile install/update/uninstall smoke test on stable Chrome.
