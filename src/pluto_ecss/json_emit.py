"""PLUTO parse tree -> structured JSON description.

Skips the Python-source layer entirely. The dict produced here is
suitable for piping into other tools (GUIs, validators, alternate
runtimes, schema checkers) or for round-tripping the procedure
structure without committing to one execution model.

Schema (informal)::

    {
      "events": [{"name": str, "description": str | None}, ...],
      "watchdog": {event_name: [statement, ...]},
      "preconditions": [statement, ...],
      "main":          [statement, ...],
      "confirmation":  [statement, ...]
    }

Each statement is a single-key-discriminator object::

    {"kind": "initiate", "call": {"verb": "switch_on", "target": "T"}}
    {"kind": "initiate_and_confirm", "call": {"verb": "switch_on", "target": "T"}}
    {"kind": "step",      "name": "N", "body": [statement, ...]}
    {"kind": "parallel",  "mode": "all" | "one", "branches": [statement, ...]}
    {"kind": "if",   "condition": str, "then": [statement, ...], "else": [statement, ...] | None}
    {"kind": "case", "expr": str, "arms": [{"when": str, "do": [statement, ...]}, ...],
                     "otherwise": [statement, ...] | None}
    {"kind": "while",  "condition": str, "body": [...], "timeout": str | None,
                       "timeout_event": str | None}
    {"kind": "for",    "var": str, "from": str, "to": str, "by": str | None, "body": [...]}
    {"kind": "repeat", "body": [...], "until": str, "timeout": str | None}
    {"kind": "wait_for_event", "event": str, "timeout": str | None}
    {"kind": "wait_until",     "condition": str, "timeout": str | None}
    {"kind": "assign", "var": str, "expr": str}
    {"kind": "log",         "value": str}
    {"kind": "inform_user", "value": str}
    {"kind": "raise",       "event": str}

Expressions are serialised as their canonical PLUTO source fragment.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from lark import Tree

from pluto_ecss._nodes import (
    CS_LABEL as _CS_LABEL_J,
    description_text as _description_text,
    is_expression as _is_expression,
    is_timeout as _is_timeout,
    name_text as _name_text,
    qname_text as _qname_text,
    render_expression as _expression_to_str,
    timeout_clause as _timeout_clause_node,
)
from pluto_ecss.parser import parse as parse_pluto


def transpile_to_json(source: str, *, indent: int | None = 2, filename: str | None = None) -> str:
    """Convert PLUTO source into a JSON string description."""
    return json.dumps(transpile_to_dict(source, filename=filename), indent=indent)


def transpile_to_dict(source: str, *, filename: str | None = None) -> Dict[str, Any]:
    """Convert PLUTO source into a dict description."""
    tree = parse_pluto(source, filename=filename)
    proc = tree.children[0]
    out: Dict[str, Any] = {
        "events": [],
        "watchdog": {},
        "preconditions": [],
        "main": [],
        "confirmation": [],
    }
    for section in proc.children:
        tag = section.data
        if tag == "declare_section":
            out["events"] = [_event_decl_to_dict(d) for d in section.children]
        elif tag == "watchdog_section":
            out["watchdog"] = {
                _name_text(h.children[0]): [_stmt_to_dict(s) for s in h.children[1:]]
                for h in section.children
            }
        elif tag == "preconditions_section":
            out["preconditions"] = [_stmt_to_dict(s) for s in section.children]
        elif tag == "main_section":
            out["main"] = [_stmt_to_dict(s) for s in section.children]
        elif tag == "confirmation_section":
            out["confirmation"] = [_stmt_to_dict(s) for s in section.children]
    return out


def _event_decl_to_dict(decl: Tree) -> Dict[str, Any]:
    out: Dict[str, Any] = {"name": _name_text(decl.children[0])}
    if len(decl.children) > 1:
        out["description"] = _description_text(decl.children[1])
    return out


def _stmt_to_dict(stmt: Tree) -> Dict[str, Any]:
    d = stmt.data
    if d == "initiate_stmt":
        out = {"kind": "initiate", "call": _activity_call_to_dict(stmt.children[0])}
        for c in stmt.children[1:]:
            if isinstance(c, Tree) and c.data == "refer_by":
                out["refer_by"] = _name_text(c.children[0])
        return out
    if d == "initiate_confirm_stmt":
        out: Dict[str, Any] = {
            "kind": "initiate_and_confirm",
            "call": _activity_call_to_dict(stmt.children[0]),
        }
        for c in stmt.children[1:]:
            if isinstance(c, Tree) and c.data == "refer_by":
                out["refer_by"] = _name_text(c.children[0])
        ct = _continuation_test_dict(stmt)
        if ct is not None:
            out["continuation_test"] = ct
        return out
    if d == "initiate_confirm_step":
        out: Dict[str, Any] = {"kind": "step", "name": _name_text(stmt.children[0])}
        sections = [
            c for c in stmt.children[1:]
            if isinstance(c, Tree) and c.data.endswith("_section")
        ]
        for section in sections:
            tag = section.data
            if tag == "declare_section":
                out["declare"] = [_event_decl_to_dict(d_) for d_ in section.children]
            elif tag == "preconditions_section":
                out["preconditions"] = [_stmt_to_dict(s) for s in section.children]
            elif tag == "main_section":
                out["body"] = [_stmt_to_dict(s) for s in section.children]
            elif tag == "watchdog_section":
                out["watchdog"] = {
                    _name_text(h.children[0]): [_stmt_to_dict(s) for s in h.children[1:]]
                    for h in section.children
                }
            elif tag == "confirmation_section":
                out["confirmation"] = [_stmt_to_dict(s) for s in section.children]
        ct = _continuation_test_dict(stmt)
        if ct is not None:
            out["continuation_test"] = ct
        return out
    if d == "parallel_all_stmt":
        return {"kind": "parallel", "mode": "all", "branches": [_stmt_to_dict(b) for b in stmt.children]}
    if d == "parallel_one_stmt":
        return {"kind": "parallel", "mode": "one", "branches": [_stmt_to_dict(b) for b in stmt.children]}
    if d == "context_stmt":
        return {
            "kind": "context",
            "target": _qname_text(stmt.children[0]),
            "body": [_stmt_to_dict(s) for s in stmt.children[1:]],
        }
    if d == "if_stmt":
        then_node = stmt.children[1]
        else_node = stmt.children[2] if len(stmt.children) > 2 else None
        return {
            "kind": "if",
            "condition": _expression_to_str(stmt.children[0]),
            "then": [_stmt_to_dict(s) for s in then_node.children],
            "else": [_stmt_to_dict(s) for s in else_node.children] if else_node else None,
        }
    if d == "case_stmt":
        arms: List[Dict[str, Any]] = []
        otherwise: List[Dict[str, Any]] | None = None
        for child in stmt.children[1:]:
            if child.data == "case_otherwise":
                otherwise = [_stmt_to_dict(s) for s in child.children]
            else:
                arms.append({
                    "when": _expression_to_str(child.children[0]),
                    "do": [_stmt_to_dict(s) for s in child.children[1:]],
                })
        return {
            "kind": "case",
            "expr": _expression_to_str(stmt.children[0]),
            "arms": arms,
            "otherwise": otherwise,
        }
    if d == "while_stmt":
        timeout = _extract_timeout(stmt)
        body = [c for c in stmt.children[1:] if not _is_timeout(c)]
        return {
            "kind": "while",
            "condition": _expression_to_str(stmt.children[0]),
            "body": [_stmt_to_dict(s) for s in body],
            "timeout": timeout,
            "timeout_event": _extract_timeout_event(stmt),
        }
    if d == "for_stmt":
        rest = list(stmt.children[1:])
        start_expr = _expression_to_str(rest[0])
        end_expr = _expression_to_str(rest[1])
        idx = 2
        by_expr = None
        if idx < len(rest) and _is_expression(rest[idx]):
            by_expr = _expression_to_str(rest[idx])
            idx += 1
        return {
            "kind": "for",
            "var": _name_text(stmt.children[0]),
            "from": start_expr,
            "to": end_expr,
            "by": by_expr,
            "body": [_stmt_to_dict(s) for s in rest[idx:]],
        }
    if d == "repeat_stmt":
        timeout = _extract_timeout(stmt)
        non_t = [c for c in stmt.children if not _is_timeout(c)]
        body = non_t[:-1]
        cond = _expression_to_str(non_t[-1])
        return {
            "kind": "repeat",
            "body": [_stmt_to_dict(s) for s in body],
            "until": cond,
            "timeout": timeout,
            "timeout_event": _extract_timeout_event(stmt),
        }
    if d == "wait_for_event":
        return {
            "kind": "wait_for_event",
            "event": _name_text(stmt.children[0]),
            "timeout": _extract_timeout(stmt),
            "timeout_event": _extract_timeout_event(stmt),
        }
    if d == "wait_until_expr":
        return {
            "kind": "wait_until",
            "condition": _expression_to_str(stmt.children[0]),
            "timeout": _extract_timeout(stmt),
            "timeout_event": _extract_timeout_event(stmt),
        }
    if d == "wait_for_time":
        return {
            "kind": "wait_for_duration",
            "duration": _expression_to_str(stmt.children[0]),
        }
    if d == "assign_stmt":
        return {
            "kind": "assign",
            "var": _name_text(stmt.children[0]),
            "expr": _expression_to_str(stmt.children[1]),
        }
    if d == "log_stmt":
        return {"kind": "log", "value": _expression_to_str(stmt.children[0])}
    if d == "inform_stmt":
        return {"kind": "inform_user", "value": _expression_to_str(stmt.children[0])}
    if d == "raise_stmt":
        return {"kind": "raise", "event": _name_text(stmt.children[0])}
    if d == "save_context_stmt":
        return {
            "kind": "save_context",
            "entries": [
                {"ref": _qname_text(entry.children[0]),
                 "local": _name_text(entry.children[1])}
                for entry in stmt.children
            ],
        }
    return {"kind": "unknown", "rule": d}


def _continuation_test_dict(stmt: Tree) -> List[Dict[str, Any]] | None:
    for c in stmt.children:
        if isinstance(c, Tree) and c.data == "continuation_test":
            return [_arm_to_dict(a) for a in c.children]
    return None


def _arm_to_dict(arm: Tree) -> Dict[str, Any]:
    label = _CS_LABEL_J[arm.children[0].data]
    return {"status": label, "action": _action_to_dict(arm.children[1])}


def _action_to_dict(action: Tree) -> Dict[str, Any]:
    kind = action.data.removeprefix("act_")
    out: Dict[str, Any] = {"kind": kind}
    if action.data == "act_raise":
        out["event"] = _name_text(action.children[0])
    elif action.data == "act_restart":
        for c in action.children:
            if isinstance(c, Tree) and c.data == "restart_max":
                out["max_times"] = _expression_to_str(c.children[0])
            elif isinstance(c, Tree) and c.data == "restart_timeout":
                out["timeout"] = _expression_to_str(c.children[0])
    return out


def _activity_call_to_dict(node: Tree) -> Dict[str, Any]:
    if node.data == "switch_on":
        verb = "switch_on"
    elif node.data == "switch_off":
        verb = "switch_off"
    else:
        return {"verb": "unknown", "rule": node.data}
    out: Dict[str, Any] = {"verb": verb, "target": _qname_text(node.children[0])}
    for c in node.children[1:]:
        if isinstance(c, Tree) and c.data == "activity_with":
            args: Dict[str, Any] = {}
            for arg in c.children:
                name = _name_text(arg.children[0])
                if arg.data == "simple_arg":
                    args[name] = _expression_to_str(arg.children[1])
                elif arg.data == "record_arg":
                    args[name] = {
                        _name_text(sub.children[0]): _expression_to_str(sub.children[1])
                        for sub in arg.children[1:]
                    }
                elif arg.data == "array_arg":
                    args[name] = [_expression_to_str(e) for e in arg.children[1:]]
            out["arguments"] = args
    return out


def _extract_timeout(node: Tree) -> str | None:
    tc = _timeout_clause_node(node)
    return _expression_to_str(tc.children[0]) if tc is not None else None


def _extract_timeout_event(node: Tree) -> str | None:
    """The event name in `with timeout T raise event E`, if any."""
    tc = _timeout_clause_node(node)
    if tc is not None and len(tc.children) > 1:
        return _name_text(tc.children[1])
    return None
