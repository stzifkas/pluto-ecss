"""Telemetry comparison operators (spec A.3.9.34): between / within / in.

Covers parse + transpile + runtime behaviour, and round-trips through the
formatter and JSON emitter.
"""
import contextlib
import io

from pluto_ecss.formatter import format_source
from pluto_ecss.json_emit import transpile_to_dict
from pluto_ecss.transpiler import transpile


def _run(src: str) -> str:
    py = transpile(src)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(compile(py, "<transpiled>", "exec"), {"__name__": "__main__"})
    return buf.getvalue()


def _proc(cond_then_else: str) -> str:
    return f"""
    procedure
      main
        {cond_then_else}
      end main
    end procedure
    """


def test_between_is_inclusive():
    out_in = _run(_proc('if 5 between 1 and 10 then inform user "hit" end if'))
    assert "hit" in out_in
    # inclusive at both ends
    assert "edge" in _run(_proc('if 10 between 1 and 10 then inform user "edge" end if'))
    # outside
    assert "miss" not in _run(_proc('if 11 between 1 and 10 then inform user "miss" end if'))


def test_within_absolute_tolerance():
    assert "ok" in _run(_proc('if 9 within 2 of 10 then inform user "ok" end if'))
    assert "no" not in _run(_proc('if 7 within 2 of 10 then inform user "no" end if'))


def test_within_percent_tolerance():
    # 95 is within 10% of 100 (tolerance band 90..110)
    assert "ok" in _run(_proc('if 95 within 10 % of 100 then inform user "ok" end if'))
    assert "no" not in _run(_proc('if 80 within 10 % of 100 then inform user "no" end if'))


def test_membership_numbers_and_strings():
    assert "hit" in _run(_proc('if 2 in (1, 2, 3) then inform user "hit" end if'))
    assert "miss" not in _run(_proc('if 9 in (1, 2, 3) then inform user "miss" end if'))
    assert "s" in _run(_proc('if "safe" in ("safe", "nominal") then inform user "s" end if'))


def test_single_element_membership_is_a_tuple():
    # regression: `X in (A)` must still be membership, not `X in A`
    assert "one" in _run(_proc('if 1 in (1) then inform user "one" end if'))


def test_round_trips_through_formatter_and_json():
    src = _proc(
        'if t between 1 and 9 then log "a" end if;'
        'if v within 5 % of 10 then log "b" end if;'
        'if m in (1, 2, 3) then log "c" end if'
    )
    once = format_source(src)
    assert format_source(once) == once
    for frag in ("between 1 and 9", "within 5 % of 10", "in (1, 2, 3)"):
        assert frag in once

    d = transpile_to_dict(src)
    conds = [stmt["condition"] for stmt in d["main"]]
    assert conds == ["t between 1 and 9", "v within 5 % of 10", "m in (1, 2, 3)"]
