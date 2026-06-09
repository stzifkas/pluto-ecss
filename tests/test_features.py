"""Tests for the scope-cut features added in the second revival pass:
if/else, case, watchdog dispatch, with-timeout."""
import subprocess
import sys
import pathlib


from pluto_ecss.transpiler import transpile


ROOT = pathlib.Path(__file__).parent.parent
EXAMPLES = ROOT / "examples"


def _run_cli(script):
    env = {"PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "pluto_ecss", "run", str(script)],
        env=env, capture_output=True, text=True, cwd=str(ROOT), check=False,
    )


def test_if_else_takes_else_branch():
    result = _run_cli(EXAMPLES / "06_if_else.pluto")
    assert result.returncode == 0, result.stderr
    assert "safe mode" in result.stdout
    assert "Switch off Reaction Wheel3" in result.stdout
    assert "nominal mode" not in result.stdout


def test_if_emits_else_branch_when_present():
    src = """
    procedure
      main
        x := 1
        if x = 1 then
          inform user "yes"
        else
          inform user "no"
        end if
      end main
    end procedure
    """
    out = transpile(src)
    assert "if " in out
    assert "else:" in out
    assert '"yes"' in out
    assert '"no"' in out


def test_case_takes_matching_arm():
    result = _run_cli(EXAMPLES / "07_case.pluto")
    assert result.returncode == 0, result.stderr
    assert "NOMINAL" in result.stdout
    assert "SAFE" not in result.stdout
    assert "UNKNOWN" not in result.stdout


def test_case_emits_if_elif_chain():
    src = """
    procedure
      main
        m := 2
        case m of
          when 1 do
            inform user "a"
          when 2 do
            inform user "b"
          otherwise
            inform user "c"
        end case
      end main
    end procedure
    """
    out = transpile(src)
    assert "_case_expr =" in out
    assert out.count("elif ") >= 1
    assert "else:" in out


def test_watchdog_handler_fires_on_raise():
    result = _run_cli(EXAMPLES / "08_watchdog.pluto")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    # ordering: Switch on, then watchdog informs, then Switch off
    assert out.index("Switch on Reaction Wheel3") < out.index("watchdog handling boom")
    assert out.index("watchdog handling boom") < out.index("Switch off Reaction Wheel3")


def test_watchdog_emits_register_call():
    src = """
    procedure
      declare
        event boom
      end declare
      watchdog
        on boom do
          inform user "handled"
        end on
      end watchdog
      main
        raise event boom
      end main
    end procedure
    """
    out = transpile(src)
    assert "register_watchdog" in out
    assert "_watchdog_" in out
    # register call comes before raise_event in emitted source
    assert out.index("register_watchdog") < out.index("raise_event")


def test_wait_for_event_with_timeout_completes():
    result = _run_cli(EXAMPLES / "09_timeout.pluto")
    assert result.returncode == 0, result.stderr
    assert "ready arrived in time" in result.stdout
    assert "counter reached 3" in result.stdout


def test_wait_for_event_with_timeout_emits_timeout_arg():
    src = """
    procedure
      declare
        event e
      end declare
      main
        wait for event e with timeout 5
      end main
    end procedure
    """
    out = transpile(src)
    assert "timeout=5" in out


def test_while_with_timeout_emits_deadline():
    src = """
    procedure
      main
        c := 0
        while c < 100 do
          c := c + 1
        end while
      end main
    end procedure
    """
    out = transpile(src)
    assert "_deadline" not in out  # no timeout, no deadline
    # add a timeout variant
    src3 = """
    procedure
      main
        c := 0
        while c < 100 do
          c := c + 1
        with timeout 1
        end while
        log "done"
      end main
    end procedure
    """
    out3 = transpile(src3)
    assert "_deadline" in out3
