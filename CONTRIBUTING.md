# Contributing

Stealth Prompt welcomes focused fixes and product improvements that preserve its
local-first, evidence-driven security boundaries.

## Development setup

```bash
python -m pip install -e ".[dev,workbench]"
cd extension
npm ci
```

Run the complete local checks before submitting a change:

```bash
ruff check .
mypy
pytest -q
cd extension
npm run lint
npm test
npm run build
```

`pytest -q` includes the real-Chromium end-to-end suite, which needs
`python -m playwright install chromium` and takes several minutes. CI does not
run it on push, so it is on you to run it locally when you touch the extension,
the Core protocol or the page executor. It skips itself when Chromium is absent,
so a green run without the browser installed proves less than it appears to.

## Change expectations

- Add tests for behaviour changes and regressions.
- Treat page content, provider output and imported scenarios as hostile input.
- Never add a model-controlled selector, URL, command or browser operation.
- Document data collection, retention or provider-sharing changes.
- Keep user-facing errors actionable and bounded.
- Update `CHANGELOG.md` for externally visible changes.

Security issues follow [SECURITY.md](SECURITY.md), not the public issue tracker.

