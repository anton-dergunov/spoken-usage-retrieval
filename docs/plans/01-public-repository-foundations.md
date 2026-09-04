# Plan 01: Public repository foundations

**Status:** Planned

**Depends on:** None

## Outcome

Make the existing prototype credible to show on public GitHub without waiting for the
multilingual, service, or ML work. A clean clone has an accurate setup path, automated verification,
clear reuse terms, and no dependency on committed corpus data.

## Current state

- The Python and React baselines work and have tests, but only manual commands run them.
- `pyproject.toml` lacks project URLs, classifiers, a console script, and development/tooling extras.
- There is no repository license or CI workflow.
- Generated `data/`, frontend dependencies/builds, and Python caches are ignored, but the README
  should explain what may be published and what normally stays local.
- The README mixes implemented behavior with a long speculative roadmap now superseded by this
  directory.

## Decisions

- License project-authored code under MIT using `Copyright (c) 2026 spoken-usage-retrieval
  contributors`. MIT allows use in a separately licensed commercial product or hosted service;
  third-party data and model licenses remain independent.
- Set Python 3.12 as the supported baseline, matching the intended Acervo runtime, and test the
  currently used Node LTS. Add broader version claims only after they are exercised in CI.
- Keep runtime dependencies minimal. Formatting, linting, typing, test, ASR, NLP, and ML tooling
  belong in named optional groups.
- Keep downloaded media/captions and derived artifacts local by default. Allow deliberately selected
  fixtures, datasets, labels, reports, or model artifacts when their licenses permit redistribution,
  their size is reasonable, and they make the research materially more reproducible.
- Avoid adding process for its own sake. One CI workflow and a few documented commands are enough;
  add more tooling only when it catches a real class of failure.

## Implementation work

1. Add `LICENSE`, `CONTRIBUTING.md`, and a short data/licensing section in the README.
2. Complete package metadata with the known repository URL, license, keywords, classifiers, and
   `speech-retrieval` console entry point. Move script dispatch behind an importable CLI module; do
   not redesign command behavior reserved for Plan 10.
3. Add a GitHub Actions workflow that installs from the lock files and runs Python tests, web tests,
   and the production web build on pull requests and the default branch. Begin with one supported
   version combination rather than an unnecessarily large matrix.
4. Add lightweight static checks appropriate to the existing codebase and pin their configuration.
   Checks must run locally through documented commands and in CI.
5. Keep environment/credential files ignored and document the default local-data layout. Do not add
   a broad repository-policy test or forbid intentionally licensed research artifacts.
6. Restructure the README around demonstrated behavior: screenshot, quick start, architecture,
   current limitations, experiment evidence, package/service direction, and a short link to the
   roadmap. Remove duplicated speculative implementation detail.
7. Add a clean-clone smoke command that uses synthetic fixtures for indexing/API checks and does not
   contact YouTube. Keep the real acquisition command documented as an explicit network operation.

## Public interfaces and data

- Add the `speech-retrieval` executable as a thin public wrapper over package functions.
- Do not change search or HTTP response shapes in this session.
- Document that generated data has no stability or compatibility guarantee until Plan 02 defines a
  schema version.

## Acceptance tests and verification

- A clean Python 3.12 and Node 20/22 checkout can install, run all tests, and build the web app using
  README commands.
- CI performs the same checks without network acquisition, API keys, local corpus data, or model
  downloads.
- Build both a wheel and source distribution and inspect them to ensure expected source/docs are
  present and caches/media are absent.
- `speech-retrieval --help` runs from the installed wheel.
- `git ls-files` contains no credentials or accidentally generated local corpus files. Any
  intentionally published data/model artifact has a documented source, license, and purpose.
- README statements agree with implemented behavior and link to `docs/plans/README.md` for future
  work.

## Non-goals

- Publishing to PyPI or npm, creating a GitHub release, or supporting multiple languages.
- Adding a daemon, new retrieval modes, audio processing, or production deployment manifests.
- Building a policy framework for every possible data or model license; evaluate concrete artifacts
  when the project has a reason to publish them.
