import pathlib
import re
import subprocess
import sys

EXAMPLES = pathlib.Path(__file__).parent.parent / "examples"
ROOT = pathlib.Path(__file__).parent.parent


def _run_cli(args, tmpdir=None):
    env = {"PYTHONPATH": str(ROOT / "src")}
    if tmpdir is not None:
        env["TMPDIR"] = str(tmpdir)
    return subprocess.run(
        [sys.executable, "-m", "pluto_ecss", *args],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )


def test_cli_run_original_script():
    result = _run_cli(["run", str(EXAMPLES / "01_original.pluto")])
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "Switch on Star Tracker2" in out
    assert "Switch on Reaction Wheel3 of AOC of Satellite" in out
    assert "Switch on Star Tracker1" in out


def test_cli_compile_writes_python():
    result = _run_cli(["compile", str(EXAMPLES / "04_events.pluto")])
    assert result.returncode == 0, result.stderr
    assert "def main():" in result.stdout
    assert 'Event("ready"' in result.stdout
    # output is valid Python
    compile(result.stdout, "<compiled>", "exec")


def test_cli_parse_prints_tree():
    result = _run_cli(["parse", str(EXAMPLES / "03_loops.pluto")])
    assert result.returncode == 0, result.stderr
    assert "while_stmt" in result.stdout
    assert "for_stmt" in result.stdout


def test_loops_example_actually_loops(capsys=None):
    result = _run_cli(["-v", "run", str(EXAMPLES / "03_loops.pluto")])
    assert result.returncode == 0, result.stderr
    # 3 while + 3 for iterations
    assert result.stderr.count("loop iteration") == 3
    assert result.stderr.count("for iteration") == 3
    assert "[INFORM] loops finished" in result.stdout


def test_run_without_keep_leaves_no_temp_residue(tmp_path):
    """`run` must delete the transpiled module when --keep is not given."""
    result = _run_cli(["run", str(EXAMPLES / "01_original.pluto")], tmpdir=tmp_path)
    assert result.returncode == 0, result.stderr
    leaked = [p.name for p in tmp_path.glob("*.py")]
    assert leaked == [], f"run leaked transpiled temp files: {leaked}"


def test_run_keep_preserves_temp_file(tmp_path):
    """`run --keep` keeps the transpiled module and prints its path."""
    result = _run_cli(["run", "--keep", str(EXAMPLES / "01_original.pluto")], tmpdir=tmp_path)
    assert result.returncode == 0, result.stderr
    match = re.search(r"\[transpiled to (.+?)\]", result.stderr)
    assert match, f"expected transpiled-path message on stderr, got: {result.stderr!r}"
    kept = pathlib.Path(match.group(1))
    assert kept.exists(), f"--keep should preserve {kept}"
    assert kept.parent == tmp_path  # written into the requested temp dir
