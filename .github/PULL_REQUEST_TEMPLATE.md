<!-- Thanks for contributing! Keep the PR focused on one change. -->

## What & why

<!-- What does this change and why? Link the issue it closes. -->

Closes #

## Change

<!-- A short summary of the approach. -->

## Checklist

- [ ] `ruff check src tests` is clean
- [ ] `pytest -q` passes
- [ ] Tests added/updated for the change (a regression test for bug fixes)
- [ ] If `src/pluto_ecss/` changed, regenerated the playground bundle
      (`python scripts/build_playground.py`) and committed `docs/playground/files.js`
- [ ] For grammar changes: handled in the transpiler, formatter, and json_emit,
      with an example and the relevant ECSS clause cited
