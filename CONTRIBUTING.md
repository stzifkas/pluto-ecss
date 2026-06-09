# Contributing to pluto-ecss

Thanks for your interest in improving pluto-ecss — a PLUTO
([ECSS-E-ST-70-32C](https://ecss.nl/standard/ecss-e-st-70-32c-test-and-operations-procedure-language/))
to Python transpiler and runtime. This guide covers local setup, the checks we
run, and what we look for in a pull request.

## Project layout

```
src/pluto_ecss/        the package
  grammar.lark         the PLUTO grammar (Lark, Earley)
  parser.py            parse + friendly errors
  transpiler.py        parse tree -> Python source
  formatter.py         canonical pretty-printer
  json_emit.py         parse tree -> structured JSON
  generator.py         YAML spec -> PLUTO source
  runtime.py           threaded runtime for transpiled output
  async_runtime.py     asyncio runtime
  _nodes.py            shared parse-tree helpers
tests/                 pytest suite
examples/              sample .pluto procedures
docs/                  mkdocs site (incl. the web playground)
```

## Setup

Python 3.9+ is required.

```bash
git clone https://github.com/stzifkas/pluto-ecss
cd pluto-ecss
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## The checks (run these before opening a PR)

```bash
ruff check src tests        # lint (E, F); CI fails on any finding
pytest -q                   # the full suite must stay green
```

Both run in CI on Python 3.9 / 3.11 / 3.13, so a PR that is red locally will be
red there too.

### Regenerating the playground bundle

The web playground ships the package source as a generated bundle. **If you
change anything under `src/pluto_ecss/`, regenerate it** or the
`test_bundle_in_sync_with_source` test will fail:

```bash
python scripts/build_playground.py
```

Commit the updated `docs/playground/files.js` alongside your source change.

## Working on the grammar

- The grammar lives in `src/pluto_ecss/grammar.lark` and is parsed with Lark's
  Earley parser.
- A new construct usually needs to be handled in **four** places: the grammar,
  the transpiler (`transpiler.py`), the formatter (`formatter.py`), and the JSON
  emitter (`json_emit.py`). Tree-reading helpers shared by the three emitters
  live in `_nodes.py` — prefer extending those over re-implementing.
- Add an example under `examples/` and a test that exercises parse + transpile
  (and runtime behaviour where it matters).
- When the feature maps to an ECSS clause, cite it (e.g. `A.3.9.34`) in the
  grammar comment and the PR.

## Pull requests

- Branch off `main`; one focused change per PR.
- Keep the diff readable and match the surrounding style.
- Write tests for new behaviour and bug fixes (a regression test that fails
  before your change is ideal).
- Use clear commit messages; conventional prefixes (`feat:`, `fix:`,
  `refactor:`, `docs:`) are appreciated.
- `main` is protected: PRs need green CI before they can merge.

## Reporting bugs and proposing features

Use the issue templates. For anything security-sensitive, follow
[SECURITY.md](SECURITY.md) instead of opening a public issue.
