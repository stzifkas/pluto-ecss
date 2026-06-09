"""Tests for record/array activity arguments (PLUTO spec A.3.9.28)."""
import pathlib
import subprocess
import sys


from pluto_ecss.parser import parse
from pluto_ecss.transpiler import transpile


ROOT = pathlib.Path(__file__).parent.parent
EXAMPLES = ROOT / "examples"


def _run_cli(*args):
    env = {"PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "pluto_ecss", *args],
        env=env, capture_output=True, text=True, cwd=str(ROOT), check=False,
    )


def test_record_arg_grammar():
    src = """
    procedure main
      initiate and confirm Switch on X
        with Tx record A := 1, B := 2 end record
        end with
    end main end procedure
    """
    tree = parse(src)
    call = tree.children[0].children[0].children[0].children[0]
    aw = [c for c in call.children if hasattr(c, "data") and c.data == "activity_with"][0]
    assert aw.children[0].data == "record_arg"


def test_array_arg_grammar():
    src = """
    procedure main
      initiate and confirm Switch on X
        with Vs array 1, 2, 3 end array
        end with
    end main end procedure
    """
    tree = parse(src)
    call = tree.children[0].children[0].children[0].children[0]
    aw = [c for c in call.children if hasattr(c, "data") and c.data == "activity_with"][0]
    assert aw.children[0].data == "array_arg"


def test_mixed_arg_shapes_in_one_with():
    src = """
    procedure main
      initiate and confirm Switch on X
        with
          Mode := "safe",
          Tx record A := 1, B := 2 end record,
          Vs array 10, 20, 30 end array
        end with
    end main end procedure
    """
    out = transpile(src)
    assert '"Mode": "safe"' in out
    assert '"Tx": {"A": 1, "B": 2}' in out
    assert '"Vs": [10, 20, 30]' in out


def test_simple_args_still_work_unchanged():
    src = """
    procedure main
      initiate and confirm Switch on X with Level := 1000, Mode := "safe" end with
    end main end procedure
    """
    out = transpile(src)
    assert 'arguments={"Level": 1000, "Mode": "safe"}' in out


def test_runtime_receives_record_and_array():
    """Handler captures whatever arguments are passed; assert shape."""
    from pluto_ecss.runtime import Activity, register_activity

    received: dict = {}

    def handler(_activity, **kwargs):
        received.update(kwargs)
        return "ok"

    register_activity(Activity("Switch on", "MyTarget", handler))

    src = """
    procedure main
      initiate and confirm Switch on MyTarget
        with
          Tx record A := 1, B := 2 end record,
          Vs array 10, 20, 30 end array
        end with
    end main end procedure
    """
    py = transpile(src)
    ns = {"__name__": "__demo__"}
    exec(compile(py, "<test>", "exec"), ns)
    ns["main"]()
    assert received == {"Tx": {"A": 1, "B": 2}, "Vs": [10, 20, 30]}


def test_example_runs_end_to_end():
    result = _run_cli("run", str(EXAMPLES / "16_record_array_args.pluto"))
    assert result.returncode == 0, result.stderr
    assert "Telecommand=" in result.stdout
    assert "ReleaseTimes=" in result.stdout
    assert "TC bundle sent" in result.stdout


def test_example_runs_in_async():
    result = _run_cli("run", "--runtime", "async", str(EXAMPLES / "16_record_array_args.pluto"))
    assert result.returncode == 0, result.stderr
    assert "TC bundle sent" in result.stdout


def test_record_array_round_trip_via_formatter():
    from pluto_ecss.formatter import format_source
    src = (EXAMPLES / "16_record_array_args.pluto").read_text()
    formatted = format_source(src)
    assert "Telecommand record" in formatted
    assert "end record" in formatted
    assert "ReleaseTimes array" in formatted
    assert "end array" in formatted
    assert format_source(formatted) == formatted


def test_record_array_json_serialisation():
    from pluto_ecss.json_emit import transpile_to_dict
    src = (EXAMPLES / "16_record_array_args.pluto").read_text()
    d = transpile_to_dict(src)
    args = d["main"][0]["call"]["arguments"]
    assert isinstance(args["Telecommand"], dict)
    assert args["Telecommand"]["ServiceType"] == "8"
    assert isinstance(args["ReleaseTimes"], list)
    assert args["ReleaseTimes"] == ["1000", "1500", "2000"]


def test_record_with_expression_values():
    """Record values are full expressions, not just literals."""
    src = """
    procedure main
      level := 1000
      initiate and confirm Switch on X
        with Tx record Power := level + 100 end record end with
    end main end procedure
    """
    out = transpile(src)
    # The record's value embeds resolve_ref for the 'level' reference
    assert '"Power":' in out
    assert "resolve_ref" in out
