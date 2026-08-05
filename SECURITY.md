# Security policy

## Supported versions

Security fixes are applied to the latest release on the default branch. Until a
stable 1.0 release, users should upgrade to the newest published version before
reporting a problem that may already be fixed.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use
[GitHub private vulnerability reporting](https://github.com/whoishacked/stealth_prompt/security/advisories/new)
and include:

- the affected version and platform;
- the security boundary that was crossed;
- minimal reproduction steps;
- impact and any known mitigations;
- whether target data or credentials may have been exposed.

The project aims to acknowledge reports within three business days and provide an
initial assessment within seven business days. These are project goals, not a paid
support SLA.

## Security boundaries

- The browser extension treats target pages and target responses as hostile.
- Core-mode provider credentials and all executable paths remain in the local Core.
- Optional Direct API credentials are session-only, never persisted, and restricted to
  fixed OpenAI or Anthropic API origins; they are still exposed to the browser process.
- The Core binds to loopback only and requires an origin-bound pairing token.
- Page mutations are restricted to a closed operation allowlist.
- A model verdict alone cannot create a confirmed finding.

See [docs/extension.md](docs/extension.md) for the full threat model and data flow.

## Authorized use

Stealth Prompt is intended only for systems the operator owns or is explicitly
authorized to assess. A vulnerability report about this project must not include
data taken from an unrelated target.
