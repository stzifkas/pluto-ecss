"""Tests for activity argument clauses (PLUTO spec A.3.9.28)."""
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


def test_grammar_parses_with_clause():
    src = """
    procedure main
      initiate and confirm Switch on Reaction Wheel3
        with Mode := "nominal", Level := 1000
        end with
    end main end procedure
    """
    tree = parse(src)
    main = tree.children[0].children[0]
    activity = main.children[0].children[0]
    aw_nodes = [c for c in activity.children if hasattr(c, "data") and c.data == "activity_with"]
    assert len(aw_nodes) == 1
    args = aw_nodes[0].children
    assert len(args) == 2
    names = [_arg_name(a) for a in args]
    assert names == ["Mode", "Level"]


def _arg_name(arg_node):
    return " ".join(str(t) for t in arg_node.children[0].children)


def test_transpile_emits_arguments_kwarg():
    src = """
    procedure main
      initiate and confirm Switch on X
        with Level := 1000, Mode := "safe"
        end with
    end main end procedure
    """
    out = transpile(src)
    assert 'arguments={"Level": 1000, "Mode": "safe"}' in out


def test_transpile_no_args_keeps_call_compact():
    src = "procedure main initiate and confirm Switch on X end main end procedure"
    out = transpile(src)
    assert "arguments=" not in out
    assert 'switch_on("X")' in out


def test_arguments_propagate_to_handler_at_runtime():
    """Register a custom Activity that captures arguments; assert it receives them."""
    from pluto_ecss.runtime import Activity, register_activity

    received: dict = {}

    def handler(_activity, **kwargs):
        received.update(kwargs)
        return "ok"

    register_activity(Activity("Switch on", "ArgTestTarget", handler))

    src = """
    procedure main
      initiate and confirm Switch on ArgTestTarget
        with Level := 1000, Mode := "safe"
        end with
    end main end procedure
    """
    py = transpile(src)
    ns = {"__name__": "__test__"}
    exec(compile(py, "<test>", "exec"), ns)
    ns["main"]()
    assert received == {"Level": 1000, "Mode": "safe"}


def test_example_runs_and_prints_with_args():
    result = _run_cli("run", str(EXAMPLES / "12_activity_args.pluto"))
    assert result.returncode == 0, result.stderr
    assert "TargetRPM=1500" in result.stdout
    assert "AsciiTag='shutdown'" in result.stdout
    assert "wheel cycled" in result.stdout


def test_async_example_runs_with_args():
    result = _run_cli("run", "--runtime", "async", str(EXAMPLES / "12_activity_args.pluto"))
    assert result.returncode == 0, result.stderr
    assert "TargetRPM=1500" in result.stdout


def test_with_clause_round_trips_through_formatter():
    from pluto_ecss.formatter import format_source
    src = (EXAMPLES / "12_activity_args.pluto").read_text()
    formatted = format_source(src)
    assert "with Mode := " in formatted
    assert "end with" in formatted
    assert format_source(formatted) == formatted


def test_with_clause_serialises_to_json():
    from pluto_ecss.json_emit import transpile_to_dict
    src = (EXAMPLES / "12_activity_args.pluto").read_text()
    d = transpile_to_dict(src)
    first_call = d["main"][0]["call"]
    assert "arguments" in first_call
    assert "Mode" in first_call["arguments"]
    assert first_call["arguments"]["TargetRPM"] == "1500"


def test_args_compose_with_refer_by_and_continuation_test():
    src = """
    procedure main
      initiate and confirm Switch on X with Level := 1 end with
        in case not confirmed: restart max 2 times; end case
      initiate Switch off X with Mode := "off" end with refer by SHUT
    end main end procedure
    """
    out = transpile(src)
    assert "arguments=" in out
    assert "_restarts_" in out
    assert 'instance_name="SHUT"' in out
