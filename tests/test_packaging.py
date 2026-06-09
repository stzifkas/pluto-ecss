"""Packaging smoke test: the Lark grammar must ship inside the built wheel.

Regression guard for the `[tool.setuptools.package-data]` key bug — the table
is keyed by the *import* package name (`pluto_ecss`), not the hyphenated
distribution name (`pluto-ecss`). With the wrong key the data file is silently
dropped and an installed copy raises `FileNotFoundError: .../grammar.lark` on
any parse.
"""
import pathlib
import subprocess
import sys
import zipfile

import pytest

ROOT = pathlib.Path(__file__).parent.parent


def test_grammar_lark_is_packaged_in_wheel(tmp_path):
    pytest.importorskip("setuptools", reason="needs setuptools to build a wheel")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(ROOT),
         "--no-deps", "--no-build-isolation", "-w", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"wheel build failed:\n{result.stderr[-800:]}"

    wheels = list(tmp_path.glob("pluto_ecss-*.whl"))
    assert wheels, "no wheel was produced"

    names = zipfile.ZipFile(wheels[0]).namelist()
    assert any(n.endswith("pluto_ecss/grammar.lark") for n in names), (
        "grammar.lark is missing from the wheel; "
        f"packaged pluto_ecss files: {[n for n in names if 'pluto_ecss/' in n]}"
    )
