"""Tests for the JSON emit backend."""
import json
import pathlib
import subprocess
import sys


from pluto_ecss.json_emit import transpile_to_dict, transpile_to_json


ROOT = pathlib.Path(__file__).parent.parent
EXAMPLES = ROOT / "examples"


def _run_cli(*args):
    env = {"PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "pluto_ecss", *args],
        env=env, capture_output=True, text=True, cwd=str(ROOT), check=False,
    )


def test_minimal_procedure_to_dict():
    src = "procedure main log \"hi\" end main end procedure"
    d = transpile_to_dict(src)
    assert d["events"] == []
    assert d["main"][0]["kind"] == "log"


def test_original_script_shape():
    src = (EXAMPLES / "01_original.pluto").read_text()
    d = transpile_to_dict(src)
    assert [e["name"] for e in d["events"]] == ["chaos", "chaos2"]
    # main has one initiate and one parallel
    kinds = [s["kind"] for s in d["main"]]
    assert "initiate" in kinds
    assert "parallel" in kinds
    parallel = [s for s in d["main"] if s["kind"] == "parallel"][0]
    assert parallel["mode"] == "all"
    assert len(parallel["branches"]) == 2
    assert all(b["kind"] == "step" for b in parallel["branches"])


def test_watchdog_serialisation():
    src = (EXAMPLES / "08_watchdog.pluto").read_text()
    d = transpile_to_dict(src)
    assert "boom" in d["watchdog"]
    handler = d["watchdog"]["boom"]
    assert any(s["kind"] == "initiate_and_confirm" for s in handler)


def test_control_flow_serialisation():
    src = (EXAMPLES / "06_if_else.pluto").read_text()
    d = transpile_to_dict(src)
    if_stmt = [s for s in d["main"] if s["kind"] == "if"][0]
    assert if_stmt["condition"]
    assert if_stmt["then"]
    assert if_stmt["else"] is not None


def test_case_serialisation():
    src = (EXAMPLES / "07_case.pluto").read_text()
    d = transpile_to_dict(src)
    case_stmt = [s for s in d["main"] if s["kind"] == "case"][0]
    assert case_stmt["expr"]
    assert len(case_stmt["arms"]) == 3
    assert case_stmt["otherwise"] is not None


def test_wait_with_timeout_serialisation():
    src = (EXAMPLES / "09_timeout.pluto").read_text()
    d = transpile_to_dict(src)
    waits = [s for s in d["main"] if s["kind"] == "wait_for_event"]
    assert waits
    assert waits[0]["timeout"] is not None


def test_json_string_is_valid_json():
    out = transpile_to_json("procedure main log \"hi\" end main end procedure")
    parsed = json.loads(out)
    assert parsed["main"][0]["kind"] == "log"


def test_all_examples_emit_valid_json():
    for path in sorted(EXAMPLES.glob("*.pluto")):
        out = transpile_to_json(path.read_text())
        json.loads(out)  # raises on bad json


def test_cli_compile_emit_json():
    result = _run_cli("compile", "--emit", "json", str(EXAMPLES / "04_events.pluto"))
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["events"][0]["name"] == "ready"


def test_cli_emit_python_default_unchanged():
    result = _run_cli("compile", str(EXAMPLES / "01_original.pluto"))
    assert result.returncode == 0, result.stderr
    assert "def main():" in result.stdout
    assert not result.stdout.lstrip().startswith("{")
