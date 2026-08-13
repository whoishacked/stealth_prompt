# Security and privacy

Stealth Prompt is local-first and has no required product account or hosted service that
receives target data.

## Trust boundaries

- The target page and every captured response are treated as hostile.
- Only the extension content executor touches the page, through a closed operation list.
- Local Core binds to loopback and requires an origin-bound pairing token.
- Core-mode executable paths and credentials never come from the target page or a model.
- Direct API keys are session-only and restricted to fixed provider API origins.
- A model verdict alone cannot create a confirmed finding.

## Chrome permissions

The extension requests `sidePanel`, `storage`, `scripting`, and `activeTab`. Host access
is optional and requested for the exact target or provider origin selected at runtime.
It does not request `cookies`, `webRequest`, `debugger`, or blanket site access at install.

## Data on this computer

Chrome local storage can contain configuration, reviewed locators, session identifiers,
and timeline metadata. Direct API reports live in extension-owned IndexedDB. Core evidence
lives in the configured artifact directory. Reports can contain sensitive target output.

The extension does not intentionally read cookies, passwords, target access tokens,
`localStorage`, or `sessionStorage`.

## Canonical policies

- [Privacy policy](https://github.com/whoishacked/stealth_prompt/blob/main/PRIVACY.md)
- [Security policy and private reporting](https://github.com/whoishacked/stealth_prompt/blob/main/SECURITY.md)
- [Detailed extension threat model](extension.md#threat-model)

Use Stealth Prompt only on systems you own or are explicitly authorized to assess.
