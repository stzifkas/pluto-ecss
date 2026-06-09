# Security Policy

## Supported versions

pluto-ecss is pre-1.0 and ships fixes on the latest release line. Security
fixes target the most recent published version on
[PyPI](https://pypi.org/project/pluto-ecss/).

| Version | Supported |
| ------- | --------- |
| latest 0.x | yes |
| older | no |

## Reporting a vulnerability

Please report suspected vulnerabilities **privately**, not in a public issue:

- Preferred: open a [private security advisory](https://github.com/stzifkas/pluto-ecss/security/advisories/new).
- Or email **stzifkas@gmail.com** with details and reproduction steps.

We aim to acknowledge a report within a few days and to agree on a disclosure
timeline once the issue is confirmed. Please give us a reasonable window to
release a fix before any public disclosure.

## Important: pluto-ecss executes generated code

This is the most relevant security consideration for users.

- `pluto-ecss run` and `pluto-ecss demo` **transpile a `.pluto` file to Python
  and execute it** in the host interpreter. Treat a `.pluto` file like a script:
  do not `run` procedures from an untrusted source.
- `pluto-ecss compile` only emits Python source and does not execute it; review
  the output before running it.

Hardening this execution path (sandboxing, an explicit opt-in flag, and an audit
of how source text reaches generated code) is tracked in the issue tracker.
If you find a concrete injection where crafted PLUTO source produces unintended
Python, please report it through the private channels above.
