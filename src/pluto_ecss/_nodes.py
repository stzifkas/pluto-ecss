"""Shared parse-tree helpers for the transpiler, formatter, and json_emit.

These walk the Lark tree produced by `pluto_ecss.parser`. They used to be
copy-pasted into all three emitters, which let them drift — notably on whether
`prop_req` counts as an expression, and the JSON emitter could not even render
a property request back to source. Keeping one copy here removes that class of
bug.

Two kinds of helper live here:

* tree readers (`name_text`, `qname_text`, `is_expression`, the continuation /
  timeout finders, the `cs_*` label map) used by every emitter, and
* `render_expression`, which turns an expression node back into canonical
  PLUTO source — shared by the formatter and the JSON emitter (which serialises
  expressions as their PLUTO source fragment). The transpiler keeps its own
  Python-emitting expression walker.
"""
from __future__ import annotations

from typing import Optional

from lark import Token, Tree


# ---- text readers ----
def name_text(node: Tree) -> str:
    """`name` node -> its words joined with spaces."""
    return " ".join(str(t) for t in node.children)


def qname_text(node: Tree) -> str:
    """`qname` node -> ``'X of Y of Z'``."""
    return " of ".join(name_text(n) for n in node.children)


def description_text(node: Tree) -> str:
    return " ".join(str(t) for t in node.children)


# ---- node classification ----
# Rule names that denote an expression. Used to tell an optional trailing
# expression child apart from a following statement (e.g. the `by` step of a
# for-loop). `prop_req` belongs here — its omission in two of the three copies
# was the original drift bug.
EXPRESSION_RULES = frozenset({
    "or_expr", "and_expr", "not_op", "comparison",
    "between_expr", "within_expr", "within_pct_expr", "in_expr",
    "arith", "term", "num_lit", "str_lit", "var_ref", "qname", "prop_req",
})


def is_expression(node: object) -> bool:
    return isinstance(node, Tree) and node.data in EXPRESSION_RULES


def is_timeout(node: object) -> bool:
    return isinstance(node, Tree) and node.data == "timeout_clause"


def timeout_clause(node: Tree) -> Optional[Tree]:
    """Return the `timeout_clause` child of a loop/wait node, or None.

    Callers emit its expression child themselves (Python vs PLUTO source).
    """
    for c in node.children:
        if is_timeout(c):
            return c
    return None


def is_continuation_test(node: object) -> bool:
    return isinstance(node, Tree) and node.data == "continuation_test"


def continuation_test_node(node: Tree) -> Optional[Tree]:
    for c in node.children:
        if is_continuation_test(c):
            return c
    return None


def restart_limit(action: Tree) -> Optional[Tree]:
    if action.data != "act_restart":
        return None
    for c in action.children:
        if isinstance(c, Tree) and c.data in ("restart_max", "restart_timeout"):
            return c
    return None


# Confirmation-status label nodes -> their PLUTO spelling (A.3.9.33).
CS_LABEL = {
    "cs_confirmed": "confirmed",
    "cs_not_confirmed": "not confirmed",
    "cs_aborted": "aborted",
}


# ---- PLUTO-source expression rendering (formatter + json_emit) ----
def render_expression(node: object) -> str:
    """Render an expression node back to canonical PLUTO source."""
    if isinstance(node, Token):
        return str(node)
    d = node.data
    if d == "num_lit":
        return str(node.children[0])
    if d == "str_lit":
        return str(node.children[0])
    if d == "var_ref":
        return qname_text(node.children[0])
    if d == "qname":
        return qname_text(node)
    if d == "prop_req":
        pr = node.children[0]
        prop_name = " ".join(str(t) for t in pr.children[0].children)
        target = qname_text(pr.children[1])
        return f"{prop_name} of {target}"
    if d == "not_op":
        return f"not {render_expression(node.children[0])}"
    if d == "or_expr":
        return " or ".join(render_expression(c) for c in node.children)
    if d == "and_expr":
        return " and ".join(render_expression(c) for c in node.children)
    if d == "comparison":
        left = render_expression(node.children[0])
        op = str(node.children[1])
        right = render_expression(node.children[2])
        return f"{left} {op} {right}"
    if d == "between_expr":
        x, lo, hi = (render_expression(c) for c in node.children)
        return f"{x} between {lo} and {hi}"
    if d == "within_expr":
        x, tol, y = (render_expression(c) for c in node.children)
        return f"{x} within {tol} of {y}"
    if d == "within_pct_expr":
        x, tol, y = (render_expression(c) for c in node.children)
        return f"{x} within {tol} % of {y}"
    if d == "in_expr":
        x = render_expression(node.children[0])
        elems = ", ".join(render_expression(c) for c in node.children[1:])
        return f"{x} in ({elems})"
    if d in ("arith", "term"):
        out = render_expression(node.children[0])
        i = 1
        while i < len(node.children):
            op = str(node.children[i])
            right = render_expression(node.children[i + 1])
            out = f"{out} {op} {right}"
            i += 2
        return out
    return f"/* {d} */"
