"""Tests for continuation tests (PLUTO spec ECSS-E-ST-70-32C A.3.9.33)."""
import pathlib
import subprocess
import sys

import pytest

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


def test_grammar_parses_basic_continuation_test():
    src = """
    procedure
      main
        initiate and confirm Switch on X
          in case
            confirmed: continue;
            not confirmed: restart;
            aborted: abort;
          end case
      end main
    end procedure
    """
    tree = parse(src)
    main = [s for s in tree.children[0].children if s.data == "main_section"][0]
    ic = main.children[0]
    assert ic.data == "initiate_confirm_stmt"
    cts = [c for c in ic.children if c.data == "continuation_test"]
    assert len(cts) == 1
    arms = cts[0].children
    assert len(arms) == 3
    labels = [a.children[0].data for a in arms]
    assert labels == ["cs_confirmed", "cs_not_confirmed", "cs_aborted"]
    actions = [a.children[1].data for a in arms]
    assert actions == ["act_continue", "act_restart", "act_abort"]


def test_grammar_parses_restart_max():
    src = """
    procedure main
      initiate and confirm Switch on X in case not confirmed: restart max 3 times; end case
    end main end procedure
    """
    tree = parse(src)
    arm = tree.children[0].children[0].children[0].children[1].children[0]
    action = arm.children[1]
    assert action.data == "act_restart"
    limit = [c for c in action.children if hasattr(c, "data") and c.data == "restart_max"][0]
    assert limit.data == "restart_max"


def test_grammar_parses_restart_with_timeout():
    src = """
    procedure main
      initiate and confirm Switch on X
        in case not confirmed: restart with timeout 5; end case
    end main end procedure
    """
    tree = parse(src)
    arm = tree.children[0].children[0].children[0].children[1].children[0]
    action = arm.children[1]
    limit = [c for c in action.children if hasattr(c, "data") and c.data == "restart_timeout"][0]
    assert limit.data == "restart_timeout"


def test_transpile_emits_retry_loop():
    src = """
    procedure main
      initiate and confirm Switch on X
        in case not confirmed: restart max 3 times; end case
    end main end procedure
    """
    out = transpile(src)
    assert "while True:" in out
    assert "_restarts_" in out
    assert "< 3:" in out
    assert "PlutoAborted" in out
    assert "PlutoTerminated" in out


def test_transpile_emits_abort_action():
    src = """
    procedure main
      initiate and confirm Switch on X in case aborted: abort; end case
    end main end procedure
    """
    out = transpile(src)
    assert 'raise PlutoAborted("aborted by continuation test")' in out


def test_transpile_emits_terminate_action():
    src = """
    procedure main
      initiate and confirm Switch on X in case not confirmed: terminate; end case
    end main end procedure
    """
    out = transpile(src)
    assert 'raise PlutoTerminated' in out


def test_transpile_emits_raise_event_action():
    src = """
    procedure
      declare event boom end declare
      main
        initiate and confirm Switch on X in case not confirmed: raise event boom; end case
      end main
    end procedure
    """
    out = transpile(src)
    assert 'proc.raise_event("boom")' in out


def test_default_action_for_missing_arm_is_abort_on_failure():
    src = """
    procedure main
      initiate and confirm Switch on X in case confirmed: continue; end case
    end main end procedure
    """
    out = transpile(src)
    # confirmed branch is explicit, not-confirmed/aborted use defaults
    assert 'raise PlutoAborted("aborted (default action)")' in out


def test_step_can_carry_continuation_test():
    src = """
    procedure main
      initiate and confirm step BRINGUP
        main
          initiate and confirm Switch on X;
        end main
      end step
        in case confirmed: continue; end case
    end main end procedure
    """
    out = transpile(src)
    assert "initiate_and_confirm_step(proc," in out
    # The retry/dispatch loop wraps the step call
    assert "while True:" in out
    assert "_cs_" in out


def test_cli_runs_spec_example():
    result = _run_cli("run", str(EXAMPLES / "10_continuation_test.pluto"))
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "Switch on Reaction Wheel3 of AOC of Satellite" in out
    assert "Gyro converter is up" in out


def test_abort_action_propagates():
    """An immediate `abort` after a successful activity is silenced
    (the activity confirmed) — but if the activity raises, abort fires."""
    from pluto_ecss.runtime import (
        Activity, PlutoAborted, register_activity,
    )

    def failing_handler(_act):
        raise RuntimeError("boom")

    register_activity(Activity("Switch on", "Faulty Box", failing_handler))

    src = """
    procedure main
      initiate and confirm Switch on Faulty Box in case not confirmed: abort; end case
    end main end procedure
    """
    py = transpile(src)
    ns = {"__name__": "__main__"}
    with pytest.raises(PlutoAborted):
        exec(compile(py, "<test>", "exec"), ns)
        ns["main"]()


def test_continuation_test_works_in_async_runtime():
    src = """
    procedure main
      initiate and confirm Switch on X in case not confirmed: restart max 2 times; end case
    end main end procedure
    """
    out = transpile(src, runtime="async")
    assert "async def main():" in out
    assert "await initiate_and_confirm" in out
    assert "_restarts_" in out


def test_all_examples_still_compile():
    for path in sorted(EXAMPLES.glob("*.pluto")):
        transpile(path.read_text())
