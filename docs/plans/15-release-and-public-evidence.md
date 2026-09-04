# Plan 15: Release and public evidence

**Status:** Planned

**Depends on:** Plans 07 and 14, plus whichever of 08–13 have completed

## Outcome

Publish a coherent release: inspected packages, a validated HTTP contract, documentation that
matches implemented behavior, and research reports that state what was measured and what was
rejected. A clean clone can reproduce the verification without private files or model downloads.

## Current state

- Plan 07 already proved the package boundary against a real consumer, so this plan is the
  public-facing pass rather than the first packaging attempt.
- Research plans produce reports and possibly model artifacts whose licensing and provenance need a
  single consistent account.
- Plan 01 established license, CI and README structure that has since drifted across many sessions.

## Decisions

- Distribute a normal Python wheel and a locally packable npm package. Registry publication is a
  release choice, not a requirement.
- Documentation describes demonstrated behavior. Optional capabilities are marked optional, and
  anything not implemented is absent rather than aspirational.
- Rejected experiments are published as negative results. A plan that ended with "this did not help"
  is a finding, and the release does not wait for every experimental model to pass its gate.
- Link experiment reports rather than turning the README into a lab log.

## Implementation work

1. Build wheel/sdist and the npm tarball, inspect their contents, and install both into clean
   temporary consumers using only documented dependencies.
2. Re-validate the versioned OpenAPI contract against the packaged TypeScript client and the
   compatibility fixture, extended to cover translation and alignment states where implemented.
3. Write concise architecture, corpus and data licensing, operations, benchmark, model-card, and
   limitations documentation, and reconcile the README with everything that changed since Plan 01.
4. Review every published artifact — fixtures, datasets, labels, reports, model files — for source,
   license, and reason, and remove or relocate anything that fails that check.
5. Run the public release checklist: clean clone, synthetic smoke corpus, tests and builds, package
   inspection, no credentials, artifact and license review, and a tagged-version dry run.
6. Update the roadmap index with final statuses, promotion and rejection decisions, and links to
   every dated report.

## Public interfaces and data

- Release artifacts are the Python wheel/sdist, the npm tarball, the OpenAPI document, optional
  external model references, and human-readable experiment and benchmark reports.
- Release notes state implemented capabilities, benchmark scope, known limitations, artifact and data
  licenses, and reproducible verification commands.

## Acceptance tests and verification

- A clean clone on supported Python and Node versions follows the README to test, build, create a
  synthetic corpus, serve it, search it, and open the demo without private files or model downloads.
- Wheel and npm consumer smokes import only documented public entry points and pass against the
  checked-in OpenAPI contract.
- Every optional capability is exercised once as unavailable, and the system degrades as documented.
- Every claim in the README and release notes maps to a test, a command, or a dated report.
- `git ls-files` contains no credentials or accidental corpus data, and every intentionally published
  artifact has a recorded source, license, and purpose.

## Non-goals

- Coupling repository release cycles with Acervo's, or moving article generation into this service.
- Public hosting infrastructure, billing, end-user authentication, or a generalized plugin system.
- Requiring every experimental model to pass its promotion gate before the baseline packages can be
  released.
