# Contributing

Contributions that improve the retrieval baseline, its reproducibility, or its documentation are
welcome. Before starting a large change, open an issue so its scope can be checked against the
[roadmap](docs/plans/README.md).

## Development setup

Use Python 3.12, Node.js 22, [uv](https://docs.astral.sh/uv/), and npm. Install exactly what the
checked-in lock files describe:

```bash
uv sync --locked --extra dev
npm ci --prefix web
```

The standard local checks are:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run speech-retrieval smoke
npm --prefix web test
npm --prefix web run build
uv build
```

Run the checks that cover your change before opening a pull request. Changes to Python or web
behavior should include focused tests. Keep command behavior and documentation in sync.

## Data, models, and credentials

Never commit API keys, cookies, tokens, personal paths, or environment files. The generated `data/`
tree is local by default and may contain third-party captions or other inputs that this project's
MIT license does not cover.

Small fixtures, datasets, labels, reports, or model artifacts may be committed when redistribution
is permitted and they materially improve reproducibility. Document the artifact's source, license,
and purpose in the same change, close to the artifact. Synthetic fixtures authored for this project
are covered by the repository's MIT license.

## Pull requests

Keep pull requests focused and explain the observable outcome, notable implementation choices, and
verification performed. Do not combine generated local corpus data or unrelated formatting with a
feature unless the formatting is required by the repository checks.
