"""PLUTO pretty-printer.

`format_source(source)` parses the input and emits a canonical PLUTO
rendering: 2-space indents, one statement per line, lowercase keywords,
events comma-separated on one line, no trailing whitespace.

The formatter is implemented as a recursive tree walker over the same
parse tree the transpiler uses, so any construct the transpiler
understands can be round-tripped through the formatter.
"""
from __future__ import annotations

from typing import List

from lark import Tree

from pluto_ecss._nodes import (
    CS_LABEL as _CS_LABEL,
    continuation_test_node as _continuation_test,
    description_text as _text_of_description,
    is_expression as _is_expression,
    is_timeout as _is_timeout,
    name_text as _text_of_name,
    qname_text as _text_of_qname,
    render_expression as _format_expression,
    timeout_clause as _timeout_clause_node,
)
from pluto_ecss.parser import parse as parse_pluto


INDENT = "  "


def format_source(source: str, *, filename: str | None = None) -> str:
    tree = parse_pluto(source, filename=filename)
    proc = tree.children[0]
    body = _format_procedure(proc, 0)
    return body.rstrip() + "\n"


def _format_procedure(proc: Tree, depth: int) -> str:
    lines: List[str] = ["procedure"]
    for section in proc.children:
        lines.extend(_format_section(section, depth + 1))
    lines.append("end procedure")
    return "\n".join(lines)


def _format_section(section: Tree, depth: int) -> List[str]:
    pad = INDENT * depth
    name = section.data
    if name == "declare_section":
        return _format_declare(section, depth)
    if name == "main_section":
        return _format_block("main", "end main", section.children, depth)
    if name == "preconditions_section":
        return _format_block("preconditions", "end preconditions", section.children, depth)
    if name == "watchdog_section":
        return _format_watchdog(section, depth)
    if name == "confirmation_section":
        return _format_block("confirmation", "end confirmation", section.children, depth)
    return [f"{pad}// unknown section: {name}"]


def _format_declare(section: Tree, depth: int) -> List[str]:
    pad = INDENT * depth
    inner = INDENT * (depth + 1)
    decls = [_format_event_decl(d) for d in section.children]
    if len(decls) <= 1:
        body = decls[0] if decls else ""
        return [f"{pad}declare", f"{inner}{body}", f"{pad}end declare"]
    # Wrap multi-event declarations: one per line, joined with commas.
    body_line = (",\n" + inner).join(decls)
    return [f"{pad}declare", f"{inner}{body_line}", f"{pad}end declare"]


def _format_event_decl(node: Tree) -> str:
    name = _text_of_name(node.children[0])
    if len(node.children) > 1:
        desc = _text_of_description(node.children[1])
        return f"event {name} described by {desc}"
    return f"event {name}"


def _format_watchdog(section: Tree, depth: int) -> List[str]:
    pad = INDENT * depth
    inner = INDENT * (depth + 1)
    lines = [f"{pad}watchdog"]
    for handler in section.children:
        ev_name = _text_of_name(handler.children[0])
        body = handler.children[1:]
        lines.append(f"{inner}on {ev_name} do")
        for s in body:
            lines.extend(_format_statement(s, depth + 2))
        lines.append(f"{inner}end on")
    lines.append(f"{pad}end watchdog")
    return lines


def _format_block(opener: str, closer: str, statements: List[Tree], depth: int) -> List[str]:
    pad = INDENT * depth
    lines = [f"{pad}{opener}"]
    for s in statements:
        lines.extend(_format_statement(s, depth + 1))
    lines.append(f"{pad}{closer}")
    return lines


def _format_statement(stmt: Tree, depth: int) -> List[str]:
    pad = INDENT * depth
    d = stmt.data
    if d == "initiate_stmt":
        head = f"{pad}initiate {_format_activity_call(stmt.children[0])}"
        for c in stmt.children[1:]:
            if isinstance(c, Tree) and c.data == "refer_by":
                head += f" refer by {_text_of_name(c.children[0])}"
        return [head]
    if d == "initiate_confirm_stmt":
        head = f"{pad}initiate and confirm {_format_activity_call(stmt.children[0])}"
        for c in stmt.children[1:]:
            if isinstance(c, Tree) and c.data == "refer_by":
                head += f" refer by {_text_of_name(c.children[0])}"
        ct = _continuation_test(stmt)
        if ct is None:
            return [head]
        return [head] + _format_continuation_test(ct, depth + 1)
    if d == "initiate_confirm_step":
        return _format_step(stmt, depth)
    if d == "parallel_all_stmt":
        return _format_parallel("in parallel until all complete", stmt, depth)
    if d == "parallel_one_stmt":
        return _format_parallel("in parallel until one completes", stmt, depth)
    if d == "context_stmt":
        target = _text_of_qname(stmt.children[0])
        body = stmt.children[1:]
        lines = [f"{pad}in the context of {target} do"]
        for s in body:
            lines.extend(_format_statement(s, depth + 1))
        lines.append(f"{pad}end context")
        return lines
    if d == "if_stmt":
        return _format_if(stmt, depth)
    if d == "case_stmt":
        return _format_case(stmt, depth)
    if d == "while_stmt":
        return _format_while(stmt, depth)
    if d == "for_stmt":
        return _format_for(stmt, depth)
    if d == "repeat_stmt":
        return _format_repeat(stmt, depth)
    if d == "wait_for_event":
        ev = _text_of_name(stmt.children[0])
        suffix = _timeout_suffix(stmt)
        return [f"{pad}wait for event {ev}{suffix}"]
    if d == "wait_until_expr":
        expr = _format_expression(stmt.children[0])
        suffix = _timeout_suffix(stmt)
        return [f"{pad}wait until {expr}{suffix}"]
    if d == "assign_stmt":
        var = _text_of_name(stmt.children[0])
        expr = _format_expression(stmt.children[1])
        return [f"{pad}{var} := {expr}"]
    if d == "log_stmt":
        return [f"{pad}log {_format_expression(stmt.children[0])}"]
    if d == "inform_stmt":
        return [f"{pad}inform user {_format_expression(stmt.children[0])}"]
    if d == "raise_stmt":
        return [f"{pad}raise event {_text_of_name(stmt.children[0])}"]
    if d == "save_context_stmt":
        entries = []
        for entry in stmt.children:
            ref = _text_of_qname(entry.children[0])
            local = _text_of_name(entry.children[1])
            entries.append(f"to {ref} by {local}")
        return [f"{pad}save context refer " + ", ".join(entries)]
    return [f"{pad}// unsupported statement: {d}"]


def _format_step(stmt: Tree, depth: int) -> List[str]:
    pad = INDENT * depth
    name = _text_of_name(stmt.children[0])
    ct = _continuation_test(stmt)
    sections = [
        c for c in stmt.children[1:]
        if isinstance(c, Tree) and c.data.endswith("_section")
    ]
    lines = [f"{pad}initiate and confirm step {name}"]
    for section in sections:
        lines.extend(_format_section(section, depth + 1))
    lines.append(f"{pad}end step")
    if ct is not None:
        lines.extend(_format_continuation_test(ct, depth + 1))
    return lines


def _format_continuation_test(ct: Tree, depth: int) -> List[str]:
    pad = INDENT * depth
    inner = INDENT * (depth + 1)
    lines = [f"{pad}in case"]
    for arm in ct.children:
        label = _CS_LABEL[arm.children[0].data]
        action_str = _format_continuation_action(arm.children[1])
        lines.append(f"{inner}{label}: {action_str}")
    lines.append(f"{pad}end case")
    return lines


def _format_continuation_action(action: Tree) -> str:
    kind = action.data
    if kind == "act_resume":
        return "resume"
    if kind == "act_abort":
        return "abort"
    if kind == "act_continue":
        return "continue"
    if kind == "act_terminate":
        return "terminate"
    if kind == "act_ask":
        return "ask user"
    if kind == "act_raise":
        return f"raise event {_text_of_name(action.children[0])}"
    if kind == "act_restart":
        limit_nodes = [c for c in action.children if isinstance(c, Tree)
                       and c.data in ("restart_max", "restart_timeout")]
        if not limit_nodes:
            return "restart"
        limit = limit_nodes[0]
        if limit.data == "restart_max":
            return f"restart max {_format_expression(limit.children[0])} times"
        return f"restart with timeout {_format_expression(limit.children[0])}"
    return f"// unsupported action: {kind}"


def _format_parallel(opener: str, stmt: Tree, depth: int) -> List[str]:
    pad = INDENT * depth
    lines = [f"{pad}{opener}"]
    for s in stmt.children:
        lines.extend(_format_statement(s, depth + 1))
    lines.append(f"{pad}end parallel")
    return lines


def _format_if(stmt: Tree, depth: int) -> List[str]:
    pad = INDENT * depth
    expr = _format_expression(stmt.children[0])
    then_node = stmt.children[1]
    else_node = stmt.children[2] if len(stmt.children) > 2 else None
    lines = [f"{pad}if {expr} then"]
    for s in then_node.children:
        lines.extend(_format_statement(s, depth + 1))
    if else_node is not None:
        lines.append(f"{pad}else")
        for s in else_node.children:
            lines.extend(_format_statement(s, depth + 1))
    lines.append(f"{pad}end if")
    return lines


def _format_case(stmt: Tree, depth: int) -> List[str]:
    pad = INDENT * depth
    inner = INDENT * (depth + 1)
    expr = _format_expression(stmt.children[0])
    lines = [f"{pad}case {expr} of"]
    otherwise = None
    for child in stmt.children[1:]:
        if child.data == "case_otherwise":
            otherwise = child
            continue
        arm_expr = _format_expression(child.children[0])
        stmts = child.children[1:]
        lines.append(f"{inner}when {arm_expr} do")
        for s in stmts:
            lines.extend(_format_statement(s, depth + 2))
    if otherwise is not None:
        lines.append(f"{inner}otherwise")
        for s in otherwise.children:
            lines.extend(_format_statement(s, depth + 2))
    lines.append(f"{pad}end case")
    return lines


def _format_while(stmt: Tree, depth: int) -> List[str]:
    pad = INDENT * depth
    expr = _format_expression(stmt.children[0])
    body = [c for c in stmt.children[1:] if not _is_timeout(c)]
    timeout = _timeout_clause(stmt)
    lines = [f"{pad}while {expr} do"]
    for s in body:
        lines.extend(_format_statement(s, depth + 1))
    if timeout is not None:
        lines.append(f"{pad}with timeout {timeout}")
    lines.append(f"{pad}end while")
    return lines


def _format_for(stmt: Tree, depth: int) -> List[str]:
    pad = INDENT * depth
    var = _text_of_name(stmt.children[0])
    start_expr = _format_expression(stmt.children[1])
    end_expr = _format_expression(stmt.children[2])
    idx = 3
    by_expr = None
    if idx < len(stmt.children) and _is_expression(stmt.children[idx]):
        by_expr = _format_expression(stmt.children[idx])
        idx += 1
    body = stmt.children[idx:]
    by_part = f" by {by_expr}" if by_expr else ""
    lines = [f"{pad}for {var} := {start_expr} to {end_expr}{by_part} do"]
    for s in body:
        lines.extend(_format_statement(s, depth + 1))
    lines.append(f"{pad}end for")
    return lines


def _format_repeat(stmt: Tree, depth: int) -> List[str]:
    pad = INDENT * depth
    non_timeout = [c for c in stmt.children if not _is_timeout(c)]
    timeout = _timeout_clause(stmt)
    body = non_timeout[:-1]
    cond = _format_expression(non_timeout[-1])
    lines = [f"{pad}repeat"]
    for s in body:
        lines.extend(_format_statement(s, depth + 1))
    lines.append(f"{pad}until {cond}")
    if timeout is not None:
        lines.append(f"{pad}with timeout {timeout}")
    lines.append(f"{pad}end repeat")
    return lines


# ---------- activity calls ----------
def _format_activity_call(node: Tree) -> str:
    if node.data == "switch_on":
        verb = "Switch on"
    elif node.data == "switch_off":
        verb = "Switch off"
    else:
        return f"// unsupported activity: {node.data}"
    target = _text_of_qname(node.children[0])
    args = _format_activity_with(node.children[1:])
    return f"{verb} {target}{args}"


def _format_activity_with(tail) -> str:
    for c in tail:
        if isinstance(c, Tree) and c.data == "activity_with":
            parts = []
            for arg in c.children:
                name = _text_of_name(arg.children[0])
                if arg.data == "simple_arg":
                    value = _format_expression(arg.children[1])
                    parts.append(f"{name} := {value}")
                elif arg.data == "record_arg":
                    rec = []
                    for sub in arg.children[1:]:
                        sub_name = _text_of_name(sub.children[0])
                        sub_value = _format_expression(sub.children[1])
                        rec.append(f"{sub_name} := {sub_value}")
                    parts.append(f"{name} record " + ", ".join(rec) + " end record")
                elif arg.data == "array_arg":
                    elements = [_format_expression(e) for e in arg.children[1:]]
                    parts.append(f"{name} array " + ", ".join(elements) + " end array")
                else:
                    parts.append(f"// unsupported arg: {arg.data}")
            return " with " + ", ".join(parts) + " end with"
    return ""


# ---------- helpers ----------
def _timeout_clause(node: Tree) -> str | None:
    tc = _timeout_clause_node(node)
    return _format_expression(tc.children[0]) if tc is not None else None


def _timeout_suffix(node: Tree) -> str:
    t = _timeout_clause(node)
    return f" with timeout {t}" if t is not None else ""
