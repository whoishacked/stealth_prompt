# Development

## Repository checks

```bash
python -m pip install -e ".[dev,workbench]"
ruff check .
mypy
pytest -q

cd extension
npm ci
npm run lint
npm test
npm run build
```

The real-Chromium integration suite is timing-sensitive and runs locally or through the
manual GitHub Actions workflow:

```bash
pytest tests/integration -q
```

## Preview the documentation

```bash
python -m venv .venv-docs
. .venv-docs/bin/activate
python -m pip install -r requirements-docs.txt
zensical serve
```

Open `http://127.0.0.1:8000/`. Before a documentation change is merged, run:

```bash
zensical build --clean --strict
```

`docs/` is the source of truth. The generated `site/` directory is ignored and must not
be committed.

## Publishing

`.github/workflows/docs.yml` builds the Markdown on every push to `main` and deploys the
static artifact with GitHub Pages. In the repository settings, choose **GitHub Actions**
as the Pages source once. Because the account site already uses `whoishacked.com`, GitHub
serves this project site at `https://whoishacked.com/stealth_prompt/`.

No change to the separate `whoishacked.github.io` Jekyll blog is required.

## Contributing

Read the [contribution guide](https://github.com/whoishacked/stealth_prompt/blob/main/CONTRIBUTING.md)
before opening a pull request. Use
[private vulnerability reporting](https://github.com/whoishacked/stealth_prompt/security/advisories/new)
for suspected security issues.
