"""Tests for step sub-bodies (PLUTO spec A.1.7).

A step mirrors a procedure: it may carry declare / preconditions /
watchdog / confirmation sections alongside main.
"""
import pathlib
import subprocess
import sys

from pluto_ecss.transpiler import transpile


ROOT = pathlib.Path(__file__).parent.parent
EXAMPLES = ROOT / "examples"


def _run_cli(*args):
    env = {"PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "pluto_ecss", *args],
        env=env, capture_output=True, text=True, cwd=str(ROOT), check=False,
    )


def test_step_with_main_only_still_works():
    """Backwards compatibility: main-only step parses and runs unchanged."""
    src = """
    procedure main
      initiate and confirm step S
        main
          initiate and confirm Switch on X;
        end main
      end step
    end main end procedure
    """
    out = transpile(src)
    assert 'switch_on("X")' in out
    assert "initiate_and_confirm_step(proc, \"S\"" in out


def test_step_with_declare_section():
    src = """
    procedure main
      initiate and confirm step S
        declare
          event s_done
        end declare
        main
          raise event s_done
        end main
      end step
    end main end procedure
    """
    out = transpile(src)
    assert "# step declarations" in out
    assert 'proc.declare_event(Event("s_done"))' in out


def test_step_with_preconditions_runs_before_main():
    src = """
    procedure main
      x := 1
      initiate and confirm step S
        preconditions
          wait until x >= 1
        end preconditions
        main
          initiate and confirm Switch on Y
        end main
      end step
    end main end procedure
    """
    out = transpile(src)
    assert "# step preconditions" in out
    assert "wait_until(lambda:" in out
    # preconditions appear before main in emitted source
    assert out.index("# step preconditions") < out.index("# step main")


def test_step_with_confirmation_section():
    src = """
    procedure main
      initiate and confirm step S
        main
          initiate and confirm Switch on Y
        end main
        confirmation
          log "done"
        end confirmation
      end step
    end main end procedure
    """
    out = transpile(src)
    assert "# step confirmation" in out
    # confirmation appears AFTER the main switch-on
    assert out.index('switch_on("Y")') < out.index("# step confirmation")


def test_step_watchdog_registers_handler():
    src = """
    procedure main
      initiate and confirm step S
        declare
          event boom
        end declare
        watchdog
          on boom do
            inform user "caught in step"
          end on
        end watchdog
        main
          raise event boom
        end main
      end step
    end main end procedure
    """
    out = transpile(src)
    assert "# step watchdog handlers" in out
    assert "_step_watchdog_" in out
    assert 'register_watchdog("boom"' in out


def test_full_step_lifecycle_example_runs():
    result = _run_cli("run", str(EXAMPLES / "14_step_sub_bodies.pluto"))
    assert result.returncode == 0, result.stderr
    assert "Switch on Star Tracker1" in result.stdout
    assert "tracker step completed" in result.stdout


def test_full_step_lifecycle_runs_under_async_runtime():
    result = _run_cli("run", "--runtime", "async", str(EXAMPLES / "14_step_sub_bodies.pluto"))
    assert result.returncode == 0, result.stderr
    assert "tracker step completed" in result.stdout


def test_step_sub_bodies_round_trip_via_formatter():
    from pluto_ecss.formatter import format_source
    src = (EXAMPLES / "14_step_sub_bodies.pluto").read_text()
    formatted = format_source(src)
    # All four step sections present
    assert "declare\n" in formatted
    assert "preconditions" in formatted
    assert "main\n" in formatted
    assert "confirmation" in formatted
    # idempotent
    assert format_source(formatted) == formatted


def test_step_sub_bodies_serialise_to_json():
    from pluto_ecss.json_emit import transpile_to_dict
    src = (EXAMPLES / "14_step_sub_bodies.pluto").read_text()
    d = transpile_to_dict(src)
    step = d["main"][1]  # main[0] is the counter assign
    assert step["kind"] == "step"
    assert step["name"] == "BRING_UP_TRACKER"
    assert "declare" in step
    assert "preconditions" in step
    assert "body" in step
    assert "confirmation" in step
    assert step["declare"][0]["name"] == "tracker_ready"


def test_step_section_order_is_preserved():
    """The transpiler emits declare -> watchdog -> preconditions -> main -> confirmation."""
    src = """
    procedure main
      initiate and confirm step S
        confirmation
          log "c"
        end confirmation
        main
          log "m"
        end main
        preconditions
          wait until 1 = 1
        end preconditions
        declare
          event e
        end declare
      end step
    end main end procedure
    """
    out = transpile(src)
    declare_at = out.index("# step declarations")
    pre_at     = out.index("# step preconditions")
    main_at    = out.index("# step main")
    conf_at    = out.index("# step confirmation")
    assert declare_at < pre_at < main_at < conf_at
