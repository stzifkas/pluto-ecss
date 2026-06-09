"""Regression tests for temp-variable collisions in nested constructs.

The transpiler used to emit a fixed `_deadline` for every `with timeout`
loop and a fixed `_case_expr` for every `case`. Nesting two of either kind
made the inner one reuse the outer one's name. For timeout loops this is an
observable bug (the inner loop overwrites the outer loop's deadline, so the
outer loop exits after one iteration). For `case` it is masked by Python's
if/elif short-circuit but still fragile, so both now get unique names.
"""
import contextlib
import io
import re

from pluto_ecss.transpiler import transpile


def _run(src: str) -> str:
    """Transpile, execute, and return whatever the procedure printed."""
    py = transpile(src)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(compile(py, "<transpiled>", "exec"), {"__name__": "__main__"})
    return buf.getvalue()


def test_nested_timeout_loops_keep_independent_deadlines():
    """The outer loop must honor its own timeout after the inner loop runs.

    With the shared-`_deadline` bug the outer loop exited after a single
    iteration; here it must complete all three.
    """
    src = """
    procedure
      main
        i := 0
        repeat
          i := i + 1
          repeat
            j := 0
          until 1 = 0 with timeout 0.02
          end repeat
          inform user i
        until i >= 3 with timeout 100
        end repeat
      end main
    end procedure
    """
    out = _run(src)
    assert out.count("[INFORM]") == 3, out


def test_nested_timeout_loops_emit_distinct_deadline_vars():
    """Codegen guard: each timeout loop gets its own `_deadline_N`."""
    src = """
    procedure
      main
        while a < 10 do
          while b < 5 do
            log "inner"
          with timeout 3
          end while
        with timeout 30
        end while
      end main
    end procedure
    """
    py = transpile(src)
    deadlines = set(re.findall(r"_deadline_\d+", py))
    assert len(deadlines) == 2, f"expected two distinct deadlines, got {deadlines}\n{py}"
    # bare `_deadline` (no suffix) must not leak back in
    assert not re.search(r"_deadline(?!_\d)", py), py


def test_nested_case_uses_distinct_scrutinee_vars():
    """Codegen guard: each `case` gets its own `_case_expr_N`, and the nested
    case still produces the correct branch."""
    src = """
    procedure
      main
        case 1 of
          when 1 do
            case 9 of
              when 9 do inform user "inner"
            end case
          when 2 do
            inform user "outer-two"
        end case
      end main
    end procedure
    """
    py = transpile(src)
    scrutinees = set(re.findall(r"_case_expr_\d+", py))
    assert len(scrutinees) == 2, f"expected two distinct scrutinees, got {scrutinees}\n{py}"
    out = _run(src)
    assert out.strip() == "[INFORM] inner", out
