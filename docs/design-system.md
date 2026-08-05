# Stealth Prompt interface guide

The Side Panel is a 320–480 px column that an operator keeps open beside a live
target. Everything below follows from that: there is never room for two things
competing for attention, and anything decorative is taking space from something
that is not.

This is an implementation guide, not a brand book. The tokens live in
`extension/src/sidepanel/sidepanel.css`; there is no design-system dependency
and no component library.

## Principles

1. **Assistant, not dashboard.** The panel walks one assessment forward. It does
   not present a wall of metrics about a system it has not tested yet.
2. **One primary action per screen.** If two actions look equally important, the
   screen has not decided what the operator should do next.
3. **State is information, not decoration.** Colour, motion and elevation are
   spent on what the run is doing. A glow that means nothing is removed.
4. **Say a thing once.** A verdict, a turn count or a blocker appears in exactly
   one place per screen. Repetition reads as two facts, and when they disagree
   it reads as a bug.
5. **Space before borders.** Group with whitespace and one hairline rule; reach
   for a bordered card only when a thing genuinely floats above the page.
6. **Never a dead end.** Every empty, error and refusal state names the next
   action.

## Tokens

### Elevation

| Token | Use |
| --- | --- |
| `--canvas` | The page behind everything. |
| `--surface` | A raised region: the session header, a report row. |
| `--surface-raised` | Controls that sit on a surface. |
| `--field` | Text inputs and selects, recessed against the surface. |

Three levels only. A fourth stops reading as depth and starts reading as noise.

### Text

| Token | Use |
| --- | --- |
| `--text` | Primary content and headings. |
| `--muted` | Supporting copy, labels, summaries. |
| `--faint` | Tertiary detail that must not compete. |

### Borders and accent

`--line` for grouping rules, `--line-strong` for interactive edges.
`--accent` / `--accent-strong` are the violet identity and mark the primary
action and the current selection. `--accent-soft` tints a selected option.

### Semantic

`--success`, `--warning`, `--danger` plus their `-soft` variants. Semantic colour
is always paired with text or a shape, never used alone: a verdict shows a dot
*and* the word.

### Scales

- Spacing: `--s1` 4, `--s2` 8, `--s3` 12, `--s4` 16, `--s5` 24, `--s6` 32.
- Radius: `--r1` 6, `--r2` 9, `--r3` 12.
- Controls: `--control` 32 px, `--control-lg` 40 px for primary actions.
- Motion: `--fast` 120 ms, `--slow` 200 ms.

## Typography

Body and controls are 12–13 px; supporting copy is 11–12 px. **Nothing is below
11 px.** Section headings are 13 px, weight 650, sentence case — not spaced
uppercase micro-labels, which shout and read poorly at this size.

Uppercase is reserved for nothing at present. If a very short tertiary label
ever needs it, it does not get letter-spacing on top.

## Component states

Every asynchronous state gets a treatment, and not all of them are a banner:

| State | Treatment |
| --- | --- |
| idle / empty | One line of copy naming the next action, plus that action. |
| connecting, discovering, generating, sending, waiting, analysing | Status text with a spinner in a **fixed-height** row, so the card does not resize as the run moves. |
| payload ready | A left accent rule on the proposal, the payload, and one primary action. |
| potential finding | A left warning rule, evidence first, decisions after. |
| confirmed / inconclusive / cancelled / timed out | A terminal summary that states why the run ended. |
| refusal | The provider's own words, bounded, labelled as a refusal rather than a payload. |
| disconnected / unsupported / stale binding | Contextual message plus the control that recovers it. |

The proposal card is **not** flooded with green when a payload arrives: green
means success, and "a payload exists" is not a success. Status lives on a 2 px
left rule so the content inside stays readable.

## Errors

Errors are contextual. Each one is filed to an area — connection, AI, target,
interaction, test, reports — and the area decides which workspace and which
group displays it. An error is one bounded actionable line, is dismissible, uses
`role="alert"`, and clears when its own retry succeeds. A success elsewhere
never clears it.

Because Setup collapses finished steps, filing an error also reopens the step it
belongs to; otherwise it would be filed correctly and still be invisible.

Ordinary recoverable errors never use a modal.

## Accessibility rules

- **Focus:** a 2 px accent ring with a 2 px canvas offset on every interactive
  element, meeting WCAG 2.2 focus appearance. One treatment, every control type.
- **Targets:** at least 24 × 24 CSS px; primary actions are 40 px tall.
- **Tabs:** the workspace switcher is a real `tablist` with `aria-selected`,
  `aria-controls`, roving `tabindex`, and Left/Right arrow navigation with focus
  following selection.
- **Dialog:** Settings is `aria-modal`, traps Tab, closes on Escape, and returns
  focus to the control that opened it.
- **Motion:** `prefers-reduced-motion: reduce` cuts every transition and
  animation. Motion is never the only indicator of a state.
- **Reflow:** the layout holds to 240 px with no horizontal scrolling, which
  covers a 480 px panel at 200 % zoom. Long origins, model names, selectors and
  provider errors wrap rather than widening the column.
- **Colour:** never the sole carrier of meaning.

## What is deliberately absent

No fake metrics, no decorative grid or scan lines, no emoji as product icons, no
gradient text, and no animated spectacle on a finding. A security tool that
dramatises its own output is harder to trust, not easier.
