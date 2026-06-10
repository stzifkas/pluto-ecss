"""Guards for the shared tree helpers (pluto_ecss._nodes).

The three emitters (transpiler, formatter, json_emit) used to keep private
copies of these helpers, which drifted: `is_expression` omitted `prop_req` in
the formatter and json_emit, and json_emit could not render a property request
back to source at all. These tests pin the single shared behaviour.
"""
from pluto_ecss import _nodes
from pluto_ecss.formatter import format_source
from pluto_ecss.json_emit import transpile_to_dict


# A for-loop whose `by` step is a property request. Detecting that optional
# expression relies on is_expression() recognising `prop_req` — the original
# drift bug would misread it as the first loop-body statement.
_FOR_WITH_PROP_BY = """
procedure
  main
    for i := 1 to execution_status of TRACKER by execution_status of TRACKER do
      log "tick"
    end for
  end main
end procedure
"""


def test_is_expression_recognises_prop_req():
    # The drift bug was that two of the three copies omitted prop_req here.
    assert "prop_req" in _nodes.EXPRESSION_RULES
    assert _nodes.is_expression(None) is False  # non-Tree input is safe


def test_formatter_is_idempotent_on_prop_req_for_loop():
    once = format_source(_FOR_WITH_PROP_BY)
    twice = format_source(once)
    assert once == twice
    # the `by` step must round-trip as an expression, not be swallowed as a statement
    assert "by execution_status of TRACKER" in once
    assert "unsupported statement" not in once


def test_json_emit_renders_property_request_in_expression():
    """json_emit used to emit `<prop_req>` for a property request in an
    expression; the shared renderer now produces real PLUTO source."""
    src = """
    procedure
      main
        if execution_status of TRACKER = "success" then
          log "ok"
        end if
      end main
    end procedure
    """
    d = transpile_to_dict(src)
    cond = d["main"][0]["condition"]
    assert "prop_req" not in cond
    assert cond == 'execution_status of TRACKER = "success"'
