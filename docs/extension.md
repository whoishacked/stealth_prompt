# The Stealth Prompt browser extension

Stealth Prompt is a browser extension that helps you run **authorized** prompt-injection
tests against a chat interface you are permitted to test. It runs in your normal
Chrome profile, so the target sees your ordinary, already-logged-in session — no
separate automation browser and no copied cookies. Provider credentials stay in Core
mode unless you explicitly choose the session-only Direct API path.

The extension is the product. The recommended local process (the **Core**) adds CLI
providers, credential isolation, deterministic checks, and evidence on disk. For a
zero-install path, the extension can instead call OpenAI or Anthropic directly with a
session-only key.

> **Authorized testing only.** You are responsible for having permission to test the
> target. The extension will not test anything you have not explicitly selected.

---

## How the pieces fit

```
┌──────────────────────────────────────────┐      ┌──────────────────────────┐
│ Chrome (your normal profile)             │      │ Local Core (Python)      │
│                                          │      │ 127.0.0.1 only           │
│  ┌────────────┐   chrome.runtime         │      │                          │
│  │ Side Panel │◄──────────────┐          │      │  ┌────────────────────┐  │
│  │  (the UI)  │               │          │      │  │ provider adapters  │  │
│  └─────┬──────┘         ┌─────▼───────┐  │      │  │ fake/claude/codex/ │  │
│        │                │  service    │  │      │  │ ollama/openai      │  │
│        │  WebSocket     │  worker     │  │      │  └────────────────────┘  │
│        └────────────────┼─────────────┼──┼─────►│  ┌────────────────────┐  │
│                         │ routing +   │  │  ws  │  │ contracts, oracles │  │
│                         │ storage     │  │      │  │ timeline, export   │  │
│                         └─────┬───────┘  │      │  └────────────────────┘  │
│                               │ inject   │      │                          │
│                     ┌─────────▼────────┐ │      └──────────────────────────┘
│                     │ content executor │ │
│                     │  (target tab)    │ │        CLI paths never enter
│                     └──────────────────┘ │        the browser. Direct API
└──────────────────────────────────────────┘        keys are session-only.
```

Three rules shape this layout:

1. **The operator chooses the trust boundary.** Core mode keeps provider credentials
   and CLI paths outside Chrome. Direct mode calls only fixed OpenAI or Anthropic API
   origins and never persists the supplied key, but the key exists in browser memory.
2. **The Core never touches the page.** Only the content executor does, and only for a
   closed set of operations.
3. **Nothing is sent without scoped authorization.** In `assist` and `guided`, every
   send is a button you press. In `auto`, the explicit **Start Auto** action authorizes only the
   displayed interaction and configured limits; the Core enforces finite bounds and
   **Stop** revokes authorization.

---

## The five-minute guided demo

One command starts the intentionally vulnerable local demo target *and* the
Core, with the demo's synthetic canary already configured as the deterministic
check:

```bash
stealth-prompt demo
```

The default target demonstrates a one-turn canary disclosure. Open the same
target with `?mode=advanced` to exercise an adaptive two-turn chain: the first
reply exposes a diagnostic path and only a different follow-up can disclose the
synthetic canary. `?mode=safe` remains the negative-control variant.

It prints the target URL, the Core port, a pairing code, the five browser steps,
and what to do when permission, pairing or binding goes wrong. Then:

1. open the printed `http://127.0.0.1:<port>/` in Chrome;
2. click the Stealth Prompt toolbar icon to open the Side Panel;
3. enter the pairing code and choose **Use current tab**;
4. press **Detect elements** and accept the suggested roles;
5. keep the **Fake** provider and press **Start**.

You never type an attack string: the first payload is generated from the
objective. The run ends with a `confirmed` verdict, because the demo discloses
the exact canary the Core was told to look for — not because a model said so.
Export the evidence (JSON + self-contained HTML) and the scenario from the panel.

Measured on a 2024 Apple-silicon laptop, the machine portion of that path —
command start, extension load, panel open, first payload generated and
approved, payload sent, streamed reply captured to completion — takes about
**5.4 seconds**. The rest of the five minutes is your clicking and reading.

`demo` binds loopback only, never opens a browser, and contacts nothing
external. Stop it with Ctrl-C; both the target and the Core shut down.

## Install

Install [Stealth Prompt from the Chrome Web Store](https://chromewebstore.google.com/detail/stealth-prompt/genafpggpdjagohhbngddncbanhpcdpm),
then pin it to the Chrome toolbar. Chrome 116 or newer is required (that is when the
Side Panel API stabilised).

For local extension development, build and load the unpacked version instead:

```bash
cd extension
npm ci
npm run build      # produces extension/dist/
```

Then:

1. Open `chrome://extensions`
2. Turn on **Developer mode** (top right)
3. Click **Load unpacked** and choose the `extension/dist` folder
4. Pin the Stealth Prompt icon to your toolbar

### Choose a connection

For Claude CLI, Codex CLI, Ollama, deterministic scorers, HTML evidence, or maximum
credential isolation, start the Core:

```bash
stealth-prompt serve
```

It prints something like:

```
Stealth Prompt Core
Listening on 127.0.0.1:17371
Pairing code: GBTE-YKNU
No deterministic checks configured: findings stay 'potential' unless you confirm
them. Add --expect-regex to enable 'confirmed'.
Open the Stealth Prompt browser extension to connect.
Press Ctrl-C to stop.
```

`serve` starts the Core and nothing else. It **never opens a browser** — you open your
own. Press Ctrl-C to stop it; that is a clean exit, not an error.

For no local service, select **Direct API** in **AI connection**, choose OpenAI or
Anthropic, enter a key, and press **Use key & load models**. Chrome asks for access to
that provider API origin. The key is held only
while the Side Panel document remains open and is never written to `chrome.storage` or
an export. This is convenience, not secure browser storage; prefer a restricted project
key with a spend limit.

Direct mode supports payload-only, Assist, Guided, and operator-bounded Auto, including all
three potential-finding policies and key-free browser-local JSON reports. It does not
provide CLI providers, deterministic scorers, scenario replay, or the Core's
self-contained HTML evidence report. Use Core mode when those assessment controls
matter.

Useful flags:

| Flag | Meaning |
|---|---|
| `--port` | Port to listen on (default `17371`; `0` picks a free one). Set the same value in the panel, next to **Connect** |
| `--host` | `127.0.0.1` or `::1`. Nothing else is accepted |
| `--artifacts-dir` | Where evidence is written |
| `--expect-regex` | A deterministic check; repeatable. See [Verdicts](#verdicts) |

---

## Pairing

Open the Side Panel and enter the pairing code the Core printed. In exchange the panel
receives a token that it uses for every later connection.

- A code is valid for **15 minutes**, and for **one** exchange.
- After **5** wrong attempts the code is dead; restart `serve` for a new one.
- The token is bound to your extension's origin. Another extension, or a web page,
  cannot use it.
- Restarting the Core invalidates the old token. Pair again with the new code.

The Core refuses any connection that is not from a `chrome-extension://` origin, so a
page you are testing cannot reach it even though it is on your machine.

---

## Running a test

Errors appear directly below the section that produced them and can be dismissed there.

1. **Choose the connection.** Use **Local Core** for CLI providers and full evidence,
   or **Direct API** for a session-only OpenAI/Anthropic key.
2. **Choose a provider and model.** Core reports what is installed and authenticated;
   direct mode loads the models available to the supplied key. Start with **Fake** in
   Core mode — it needs no credentials and no network.
   External providers show a persistent data-processing warning before the run controls.
3. **Choose a behavior and objective.** Behavior determines whether the extension only
   generates, asks before every send, prepares follow-ups, or runs a bounded adaptive
   loop. Objectives include *instruction disclosure* and *sensitive data disclosure*.
4. **Choose the response trigger.**
   - **Capture from page** watches the selected response container.
   - **Paste response** is the fallback for canvas, virtualized, cross-frame, or
     otherwise unreliable chat output. In this case only the input and send control
     are required.
5. **Select the page elements.** Start with **Detect elements**. It proposes the
   input, send control, and response container from DOM semantics and shows a
   confidence score. Detection is read-only: review the suggestions and save them
   before any page mutation is possible. If a suggestion is missing or wrong, use
   the manual Select buttons and click, in the target tab:
   - the input where you type,
   - the control that sends,
   - for **Capture from page**, one example of an assistant reply.

   Chrome will ask for access to that one site. The extension asks for exactly the
   origin you selected — never for all sites.

   Open the target tab and invoke Stealth Prompt from its toolbar icon. That click both
   opens the Side Panel and records the current tab as the target under Chrome's
   `activeTab` permission. **Use current tab** can refresh the binding afterward. The
   first **Select** action requests persistent site access directly from that click, as
   required by Chrome. If access is denied, the panel shows the reason and does not
   attempt to inject the executor.
6. **Save the binding.** The panel validates the selectors and tells you if one is
   ambiguous or missing. **Fill harmless test draft** verifies the input without
   pressing Send.
7. **Press Start.** The first payload is generated for you from the objective. You never
   have to write an opening instruction.
8. In `assist` or `guided`, **review, edit if you want, then approve**. In `auto`, review
   the limits and grant the one-time run authorization. Nothing is typed before the
   applicable authorization.
9. The reply is captured or pasted, evaluated, and the next step proposed.

While the selected runtime is generating or evaluating, the Proposal card has an amber border,
spinner, phase label, and elapsed time. A successfully generated payload changes it to
green. The status follows runtime events, not a guessed browser timeout.

If **Start** cannot proceed, it still works: pressing it shows a checklist of what is
missing and what to do about it. There is no button that is mysteriously greyed out.

### Modes

| Mode | Generates payloads | Types into the page | Sends |
|---|---|---|---|
| `payload_only` | yes | **never** | never |
| `assist` (default) | yes | on approval | on approval |
| `guided` | yes, including the next step automatically | on approval | on approval |
| `auto` | yes, adapting after every reply | after one explicit start authorization | automatically, within the configured policy |

`guided` differs from `assist` only in that it proposes the follow-up payload without
being asked. **It still requires your approval for every send.**

`auto` runs the full propose → send → capture → evaluate loop. Its **Start Auto** button
shows the send policy. The default is 20 turns with no wall-clock limit. Turns may be
set from 1 to 100 or Unlimited; time may be Unlimited or explicitly limited in
**Settings**.
Auto requires **Capture from page** and `redacted` or `full` sharing so the AI can adapt
to replies. Core enforces those limits in Core mode; direct mode applies the same bounds
inside the extension. **When a potential finding appears** selects one policy:

- **Pause for review** opens Review and waits for your confirm-or-continue decision;
- **Stop and save report** ends at the first potential signal without mislabelling it
  as confirmed;
- **Continue and record** keeps testing and preserves every turn for the final report.

Unlimited turns cannot be combined with **Continue and record**: choose Pause or Stop.
If both turns and time are Unlimited, the panel warns that token use remains unbounded
until a finding triggers the selected policy or you press Stop.

A deterministic confirmation always stops. The turn or duration limit also ends the
run, saves the report automatically and opens Reports. **Continue +N turns** remains
available after a turn-limit stop, and **Continue auto run** resumes a time-limit stop.
With review selected, the already prepared follow-up is retained, so continuing does
not spend another provider turn.
Reloading the panel never restores automatic-send authorization.
Provider work runs without blocking the control channel: **Cancel** or **Stop** fences
late output, interrupts the active adapter, and prevents it from authorizing a send.

`payload_only` is enforced in the service worker, not just hidden in the UI: the worker
is the single chokepoint every page mutation passes through, and it refuses `fill` and
`submit` outright while that mode is selected.

### Manual response trigger

Use **Paste response** when the response container cannot be selected reliably. Paste
the latest bot reply and press **Analyze & generate next payload**. This is recorded as
an operator-supplied observation in evidence; it is never falsely labelled as a browser
capture. The pasted text is not stored in `chrome.storage`, and the selected sharing
policy still applies before any text is sent to a provider.

Manual response input is intentionally incompatible with `auto`: a run that waits for
human copy/paste is not autonomous. Use `assist` or `guided`, or fix the page response
binding before selecting Auto.

### Verdicts

| Verdict | Meaning |
|---|---|
| `not_observed` | No evidence of the objective in the reply |
| `potential` | The model thinks something happened, but nothing proves it |
| `confirmed` | A deterministic check matched, **or** you confirmed it yourself |
| `inconclusive` | The attempt could not be assessed (e.g. capture timed out) |

**A model's opinion alone can never produce `confirmed`.** That requires either a
`--expect-regex` match or your explicit confirmation. Without any `--expect-regex`, the
best an automated run can reach is `potential` — that is a correct result, not a
malfunction.

Use canaries you planted yourself:

```bash
stealth-prompt serve --expect-regex 'SP_CANARY_[A-Z0-9]{12}'
```

### Data sharing

The **sharing** setting controls how much of the target's reply is sent to the AI
provider that is helping you:

| Setting | The provider sees |
|---|---|
| `none` (default) | Nothing from the reply — only your objective and the payload history |
| `redacted` | The reply with credential-shaped strings stripped |
| `full` | The reply verbatim |

The default is `none`. Raise it only when you are comfortable that the target's replies
may leave your machine.

### Evidence

**Export** writes two files into the session artifact directory:

- `session.json` is versioned, machine-readable evidence;
- `report.html` is a self-contained, script-free report with scope, configuration,
  attack chain, hashes, assessment and timeline.

Both may contain sensitive target content. Files are created with owner-only
permissions where the operating system supports them. Review the report before
sharing it.

---

## Workspaces

The Side Panel is a narrow column, so it is organised as four separate
workspaces rather than one scrolling document. Only one is rendered at a time:
switching does not scroll, it replaces what is on screen.

| Workspace | What it holds |
| --- | --- |
| **Setup** | Connection, provider and model, data sharing, target tab, interaction binding, behaviour and objective, the readiness summary, and one primary action. |
| **Test** | A compact session header, the current run state, the editable payload, approve/regenerate/copy/cancel/stop, the manual-response fallback, and a short live timeline. |
| **Review** | The verdict, summary, observed signals, the prepared next payload, and the three decisions only a human may make. |
| **Reports** | This session's evidence summary and export actions, plus the stored report library in Core mode. |

**Settings** is a secondary workspace for turn and optional time limits, the advanced
instruction, and scenario import/export.

The switcher is a real ARIA `tablist`. Left/right arrows move between reachable
workspaces and focus follows the selection; leaving Settings returns focus to the
control that opened it. A workspace with nothing to show — Test before a run,
Review before an analysis — is disabled with a reason rather than empty.

Setup is progressive. The step you are on is expanded; finished steps collapse
to a one-line summary with a **Change** button, and steps you have not reached
are listed but not opened. That keeps the primary action reachable without
scrolling on a 320 px panel, and it stops every blocker being printed twice —
once beside its own control and again in a summary.

An error always reopens the step it belongs to, so a contextual message can
never be filed correctly and still be invisible.

The interface tokens, states and accessibility rules are documented in
[the interface guide](design-system.md).

### Automatic transitions

The active workspace is never stored. It is computed from the assessment plus
whatever the operator last asked for, which is why a reload lands somewhere
sensible instead of somewhere stale:

| When | The panel opens |
| --- | --- |
| Nothing configured yet | Setup |
| A run starts | Test |
| A potential finding with Pause for review selected | Review |
| Auto stops on a finding or reaches a bound | Reports, with the saved result |
| The run is confirmed, stopped, or finished | Reports, with a terminal summary |
| The binding goes stale mid-run | Test, with a link to the Setup step that fixes it |
| A reload with a run still open | Test |

An explicit choice wins while it remains reachable, so you can read Reports
during a run without being pulled back. Two things never happen implicitly:
a paused finding is never resolved for you, and automatic sending is never
re-authorized by a navigation, a reload, or a scenario import.

### Where errors appear

An error is filed against the control that caused it, and the panel shows it
there:

| Failure | Appears beside |
| --- | --- |
| Pairing, port, socket | Connection, in Setup |
| Provider, model, credential | AI, in Setup |
| Target tab, host permission | Target, in Setup |
| Picker, binding, stale locator | Interaction, in Setup |
| Generation, send, capture, analysis | The live Test workspace |
| Listing or opening a stored report | Reports |

Every error is one bounded actionable line, is dismissible, and clears when its
own retry succeeds. A success in one section never clears an unrelated error
that is still unresolved.

## Binding health

A binding is a set of locators, and a page can change underneath it at any time.
The panel therefore shows how much it currently trusts the saved binding:

| State | Meaning |
| --- | --- |
| **Healthy** | Every bound element resolves on the current document. |
| **Re-checking** | The page changed; the elements are being verified. |
| **Needs review** | At least one role stopped resolving, or is ambiguous. Sending is blocked. |
| **Unsupported** | The interaction cannot be reached at all — see the limits below. |

Revalidation runs after a target reload, after a same-origin navigation, after
an SPA route change (observed through the tab's own URL updates, not by polling
the DOM), when the panel is reopened, and **immediately before every fill or
submit**. Repeated navigation signals are debounced into one check.

Two properties matter more than the indicator itself:

- **Health checking never mutates the page.** Asking "does this still resolve?"
  is a read-only query, so merely watching a target is not an interaction with it.
- **A failed check fails closed.** It pauses an automatic run, revokes the
  transient automatic-send authorization, and blocks the mutation at the service
  worker — the same chokepoint that enforces payload-only. A stale or ambiguous
  locator stops the send rather than acting on the wrong element.

A binding that goes unhealthy is **not** erased. The reviewed locators stay so
you can see which role broke; the panel names it and offers **Re-check**,
**Detect elements**, and per-role manual picking.

### Known limits

Discovery and capture are deliberately read-only DOM work, so some interfaces
cannot be bound:

- **nested cross-origin iframes** — the extension holds one origin at a time and
  does not automate across frame boundaries;
- **closed Shadow DOM** — its content is unreachable from a content script by
  design;
- **canvas-only interfaces** — there are no elements to bind;
- **heavily virtualized response lists** — rows are recycled, so a captured
  container may be reused for a later message.

These report as **Unsupported** rather than looping on revalidation, because
re-detecting cannot fix them.

## Reviewing suggested elements

**Detect elements** proposes a locator for each role and reports **confidence
per role**, not one aggregate. An aggregate hides the case that matters: a
confident input and a guessed response container average to "medium", and the
response container is what decides the verdict.

Each suggestion carries a short reason, a **Highlight** button that outlines the
element in the page, and independent **Accept** / **Pick manually** actions.
Nothing is ever saved automatically — a heuristic that got two roles right and
one wrong should not force an all-or-nothing decision.

## Scenarios

A scenario records *how an authorized assessment was set up* so it can be
reproduced. It is exported separately from the evidence, and the two files are
independent: you can share a scenario when you cannot share the transcript.

A scenario carries the schema version, name and description, objective,
provider *kind* and requested model, mode and limits, sharing policy, target
origin, potential-finding policy, the reviewed binding, and the deterministic
scorer configuration.

It never carries credentials, cookies, storage, headers, tokens, or captured
responses. The parser **refuses** a credential-shaped field rather than dropping
it quietly, because silently discarding one would teach operators that putting a
secret there is safe.

Importing is two steps. The Core parses the file and returns a preview; you read
it — including an explicit warning when the recorded origin differs from the tab
you are on — and only then apply it. A one-step import could silently retarget a
live assessment at a host you never agreed to touch.

Two things an import never does:

- **it never restores automatic-send authorization.** That authorization is
  transient and is not representable in a file. Auto must be re-armed by hand.
- **it never trusts the recorded binding.** Replay requires current host
  permission and a fresh validation against the live document.

An unknown schema version is a distinct, named error rather than a generic parse
failure, so "made by a newer build" does not read as "corrupt file".

## Scorers

Only a deterministic scorer — or your explicit confirmation — can make a finding
`confirmed`. A model's judgement is capped at `potential`. The available scorers:

| Scorer | Reads | Confirms on |
| --- | --- | --- |
| Fragment | captured reply | an exact substring |
| Regex | captured reply | a pattern match |
| Structured | a JSON field in the reply | a match *inside that field* |
| DOM | a read-only observation of the bound target | a match in the observed text |
| Navigation | the target's own URL | an origin/path pattern |
| Human | your explicit confirmation | nothing else |

Every configured scorer produces a result for every turn — including the ones
that did not match and the ones that could not run — with its id, type, status,
bounded evidence preview, SHA-256 of the matched value, deterministic flag,
reason, timestamp and turn id. Both reports show all of them.

That completeness is the point: a report listing only matches cannot distinguish
"checked and found nothing" from "never checked", and those justify very
different confidence in a `not_detected` verdict. A scorer whose input is
missing reports **Not applicable** with a reason, never a clean negative.

Malformed rules — an uncompilable regex, a structured assertion with no field
path — are rejected when the scenario is imported or the Core is started, so a
run never begins with a check that cannot execute.

## The report library

In **Core mode**, Reports lists what is actually on disk. The index is derived
from the artifact store — each run already writes its own directory containing
`session.json` and `report.html` — and it is recomputed on every visit rather
than cached. A cached index could claim a report still exists after you deleted
the directory, which is worse than no index.

Only bounded metadata crosses the socket while listing. **View results** then
loads `session.json` on demand and shows the verdict, configuration, payloads,
responses, and evaluations inside Reports. Every field is bounded and rendered
as text. The self-contained HTML report is never injected into the panel,
because it contains target output and must never be given a script context;
HTML and JSON remain available as explicit downloads.

Deletion is deliberately not implemented in this pass. Removing evidence is not
an action to ship half-built, and a delete button that silently failed — or
removed the wrong directory — would be worse than none.

In **Direct API mode** there is no Core artifact directory. The extension instead
keeps up to 50 bounded reports in IndexedDB belonging to this Chrome profile.
Reports opens their payloads, selected target responses and evaluations in the same
safe text-only viewer, and provides JSON download and per-report deletion. The API
key is never included. Removing the extension removes this browser-local history.

### Protocol messages

Both are versioned frames on the existing paired loopback socket.

| Frame | Direction | Payload |
| --- | --- | --- |
| `reports.list` | panel → Core | `limit` (1–200) |
| `reports` | Core → panel | `reports[]` of bounded metadata, `root`, `truncated` |
| `reports.open` | panel → Core | `report_id`, `artifact` |
| `report` | Core → panel | `report_id`, `artifact`, `path`, `content` |

`report_id` must match the Core's own directory pattern and `artifact` must be
one of `report.html`, `session.json`, `scenario.json`. The resolved path is
re-checked to be inside the artifacts root, so a crafted id and a symlink
planted in the directory are both refused with `unknown_report`. Listings are
length-capped and every field is parsed and bounded again on the extension side.

## What the extension stores, and what it never stores

Stored (in `chrome.storage`, on your machine):

- your provider, model, mode, response trigger, finding policy, limits, sharing, and objective choices
- the selectors you picked, and the origin they belong to
- session, tab, document, binding, turn, and operation identifiers
- the timeline of what happened

The manual-response textarea itself is not persisted in `chrome.storage`. When you
submit it, the text goes to the selected runtime and is shared with the provider only
according to the `none` / `redacted` / `full` policy. Core mode may include it in the
configured evidence export. Direct mode stores the bounded assessment transcript in
extension-owned IndexedDB so it can be reopened from Reports; this history is limited
to 50 reports and supports JSON download and deletion.

Never read or stored by the extension:

- cookies
- access tokens belonging to the target
- the page's `localStorage` or `sessionStorage`
- passwords
- API keys or provider credentials in `chrome.storage`, bindings, timelines, or exports

In Direct API mode the currently entered key exists temporarily in the Side Panel and
service worker memory while a request is active. Closing the panel clears it. It is not
recoverable on reload.

### Surviving a reload

Reloading the panel, or navigating the target within the same origin, does **not**
destroy your session. A new document gets a new document id; the session id, settings,
binding, and timeline are kept. Navigating to a *different* origin invalidates the
binding — the selectors were chosen for a specific site — but leaves the session intact
so you can rebind.

---

## Permissions, and why each is needed

| Permission | Why |
|---|---|
| `sidePanel` | The UI is a Side Panel |
| `storage` | Remembering settings and the session across reloads |
| `scripting` | Injecting the executor into the tab you selected |
| `activeTab` | Identifying the tab you invoked the extension on |
| `optional_host_permissions` | Access to the selected target origin and, in Direct API mode, the selected fixed provider API origin |

The extension deliberately does **not** request `tabs`, `cookies`, `webRequest`, or
`debugger`. Its manifest declares optional HTTP(S) host patterns so Chrome can present
a runtime grant, but the extension requests and receives only the target or provider
origin you approve; there is no blanket host access at install.

The Content Security Policy is `script-src 'self'; object-src 'none'; base-uri 'none'`.
There is no `eval`, no `new Function`, no inline script, and no remote code. The AI model
never produces anything executable — it produces text that you review and approve. The
set of browser operations is a closed allowlist (`pick`, `validate`, `fill`, `submit`,
`capture`, `snapshot`, `conversation`, `highlight`); anything else is refused before it
reaches the page.

---

## Threat model

What this design defends against:

- **A malicious target page reaching the Core.** The Core requires a
  `chrome-extension://` Origin and a paired token; a page has neither. It also refuses
  any bind that is not on loopback.
- **A target page issuing operations.** The service worker accepts messages only from
  our own extension pages, checked by the sender's origin — which the browser stamps and
  a page cannot forge.
- **Prompt injection turning into code execution.** The model's output is only ever
  treated as *text to send*. Operations are a fixed allowlist, and a provider refusal is
  classified as a refusal, never mistaken for a payload.
- **Silent modification of what you reviewed.** An oversized payload is rejected rather
  than truncated, and the evidence records a hash of what was actually approved.
- **Overclaiming.** `confirmed` requires deterministic evidence or your confirmation.

What it does **not** defend against, and you should keep in mind:

- Anyone who can run code as your user can read `chrome.storage`, artifacts, and a
  Direct API key while it is present in the browser process.
- The Core trusts whatever provider credentials are in its environment.
- Direct API mode intentionally gives the extension process the supplied provider key.
- Choosing `full` sharing sends target replies to your AI provider. That is your call.
- A pairing token lives in extension storage until the Core restarts.

---

## Troubleshooting

**The panel says it cannot reach the Core.**
Is `stealth-prompt serve` still running? It must be on the same machine. If you started
it with `--port`, set the same port in the box next to **Connect** — the panel remembers
it across reloads.

**Pairing is rejected.**
Codes expire after 15 minutes and work once. Restart `serve` for a fresh code. Note that
`O`/`0` and `I`/`1` are not used in codes, so you cannot confuse them.

**"Could not establish connection. Receiving end does not exist."**
The executor is not in the page yet. Reload the target tab. If it persists, confirm you
granted access to that site.

**A selector "no longer matches" or "is ambiguous".**
The page changed, or your selector matches several elements. Re-pick the element. The run
pauses rather than acting on the wrong element.

**Everything says `potential` and nothing is ever `confirmed`.**
Expected without deterministic checks. Add `--expect-regex` with a canary you planted, or
confirm the finding yourself.

**Capture times out.**
The reply selector may point at the wrong element, or the target is slower than the
capture timeout. Re-pick an example reply.

**Claude or Codex generation feels slower than using the CLI in a terminal.**
The panel waits for a complete, schema-valid JSON decision before it exposes a payload;
a terminal can feel faster because it paints the first streamed token immediately.
The green timing note above a generated payload shows the measured provider operation,
so capture time and model time are distinguishable. Follow-up turns combine reply
analysis and the next proposal in one model call, and the CLI adapters use a minimal
system prompt with low reasoning effort. The selected model still dominates latency;
choose a faster model in **AI** if the remaining delay is too high.

**Select input/send/response appears to do nothing.**
Activate the target tab and click the Stealth Prompt toolbar icon once more; this is the
gesture that grants temporary `activeTab` access and records the target. Then press a
Select button and approve Chrome's per-site access request. Errors are shown near the
top of the panel. Controls inside iframes are not selectable in this release; the picker
detects that case and reports it instead of waiting forever.

---

## Development

```bash
cd extension
npm ci
npm run lint     # type-checks shipped code and tests
npm test         # unit tests (node --test)
npm run build    # dist/
```

After a rebuild, press **Reload** on `chrome://extensions` to pick up the new code.

The manifest is a static file, checked in at `extension/manifest.json` and copied to
`dist/` unchanged — it is not generated per run.

The generated icon source is `extension/icons/icon-source.png`. The build ships only
the audited 16, 32, 48, and 128 px variants referenced by the manifest.

The content script is built as an **IIFE**, not an ES module: `chrome.scripting`
injects classic scripts, so an ESM bundle would fail to parse on its `export` statement
and no listener would ever register.

Python-side checks:

```bash
pytest -q
ruff check .
mypy src/stealth_prompt tests
```

### Real-browser tests

`tests/integration/test_extension_e2e.py` loads `extension/dist` unpacked in Chromium
and drives the real panel, worker, and executor against a real Core.

Two harness notes:

- A Side Panel cannot be opened programmatically (Chrome requires a user gesture), so
  the panel is loaded as an extension page in a tab. Same document, same script, same
  `chrome.*` APIs — only the container differs.
- Tests that drive a page load a *copy* of the build whose manifest names
  `http://127.0.0.1/*` as a static host permission. Headless Chrome cannot display the
  consent bubble that `chrome.permissions.request` opens, and the grant lives in the
  profile's MAC-signed `Secure Preferences`, so it cannot be seeded. Only permission
  *acquisition* is bypassed; the shipped manifest is still held to the least-privilege
  rule by its own test.

---

## Relationship to the Workbench

The CLI-launched browser **Workbench** (`stealth-prompt workbench`) is **deprecated**. It
still works and its tests still run, but the extension is where the work continues.

| | Workbench | Extension |
|---|---|---|
| Browser | A separate automation Chromium | Your normal Chrome profile |
| Session | You log in again in the automation browser | Already logged in |
| Launch | The CLI opens a browser for you | You open your own; `serve` never does |
| UI | A local web page | MV3 Side Panel |

To migrate: build and load the extension, run `stealth-prompt serve` instead of
`stealth-prompt workbench`, and pick your elements again in the panel. Objectives,
sharing settings, verdict rules, and the evidence format are the same.
