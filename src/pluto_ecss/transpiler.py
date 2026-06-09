"""PLUTO parse tree -> Python source code.

Emits a self-contained .py file that imports from `pluto_ecss.runtime` and
defines a `main()` function. The transpiled module is human-readable.
"""
from __future__ import annotations

import pathlib
from typing import List

from lark import Token, Tree

from pluto_ecss.parser import parse as parse_pluto

_RUNTIME_DIR = pathlib.Path(__file__).parent


INDENT = "    "


class TranspileError(Exception):
    pass


VALID_RUNTIMES = ("threaded", "async")
VALID_STYLES = ("functions", "class")


def transpile(
    source: str,
    *,
    module_doc: str | None = None,
    filename: str | None = None,
    runtime: str = "threaded",
    style: str = "functions",
    no_runtime: bool = False,
) -> str:
    """Transpile a PLUTO source string into runnable Python source.

    `runtime`:
      - "threaded" (default): emits against `pluto_ecss.runtime`, threads-based.
      - "async": emits against `pluto_ecss.async_runtime`, asyncio-based.

    `style`:
      - "functions" (default): emits `def main():` (or `async def main():`).
      - "class": emits a `TranspiledProcedure(Procedure)` subclass with
        declarations in `__init__` and main body in `run()`.

    `no_runtime`:
      - False (default): the output `import`s from `pluto_ecss.runtime` (or
        `pluto_ecss.async_runtime`).
      - True: inlines the runtime source so the output is a single
        self-contained .py file with no `pluto-ecss` dependency.
    """
    if runtime not in VALID_RUNTIMES:
        raise TranspileError(f"unknown runtime: {runtime!r}; expected one of {VALID_RUNTIMES}")
    if style not in VALID_STYLES:
        raise TranspileError(f"unknown style: {style!r}; expected one of {VALID_STYLES}")
    tree = parse_pluto(source, filename=filename)
    emitter = _Emitter(runtime=runtime, style=style)
    body = emitter.emit_procedure(tree.children[0])
    header = _module_header(module_doc, runtime=runtime, no_runtime=no_runtime)
    return header + body + _module_footer(runtime=runtime, style=style)


_RUNTIME_PUBLIC_NAMES = (
    "Procedure", "Event",
    "PlutoAborted", "PlutoTerminated",
    "switch_on", "switch_off",
    "initiate", "initiate_and_confirm", "initiate_and_confirm_step",
    "parallel_until_all", "parallel_until_one",
    "wait_for_event", "wait_until",
    "inform_user", "pluto_log",
)


def _module_header(doc: str | None, *, runtime: str, no_runtime: bool = False) -> str:
    doc_str = f'"""{doc}"""\n' if doc else '"""Transpiled from a PLUTO procedure."""\n'
    if no_runtime:
        runtime_file = "async_runtime.py" if runtime == "async" else "runtime.py"
        runtime_src = (_RUNTIME_DIR / runtime_file).read_text()
        return (
            doc_str
            + "# --- inlined pluto-ecss runtime (--no-runtime) ---\n"
            + "# This file is self-contained. Edit at your own risk.\n"
            + runtime_src.rstrip()
            + "\n# --- end inlined runtime ---\n\n\n"
        )
    module = "pluto_ecss.async_runtime" if runtime == "async" else "pluto_ecss.runtime"
    import_list = ",\n    ".join(_RUNTIME_PUBLIC_NAMES)
    return (
        doc_str
        + f"from {module} import (\n    {import_list},\n)\n\n\n"
    )


def _module_footer(*, runtime: str, style: str = "functions") -> str:
    invocation = (
        "TranspiledProcedure().run()" if style == "class" else "main()"
    )
    if runtime == "async":
        return (
            "\n\nif __name__ == \"__main__\":\n"
            + INDENT + "import asyncio\n"
            + INDENT + f"asyncio.run({invocation})\n"
        )
    return "\n\nif __name__ == \"__main__\":\n" + INDENT + f"{invocation}\n"


def _join(lines: List[str], depth: int) -> str:
    pad = INDENT * depth
    return "\n".join(pad + ln if ln else ln for ln in lines)


def _text_of_name(node: Tree) -> str:
    """name node -> single string (words joined with spaces)."""
    return " ".join(str(t) for t in node.children)


def _text_of_qname(node: Tree) -> str:
    """qname node -> 'X of Y of Z' string."""
    return " of ".join(_text_of_name(n) for n in node.children)


def _text_of_description(node: Tree) -> str:
    return " ".join(str(t) for t in node.children)


class _Emitter:
    def __init__(self, *, runtime: str = "threaded", style: str = "functions") -> None:
        self._step_counter = 0
        self._runtime = runtime
        self._is_async = runtime == "async"
        self._style = style
        self._is_class = style == "class"
        # `proc` for function style, `self` when the body is a method.
        self._receiver = "self" if self._is_class else "proc"
        # Stack of `in the context of X do … end context` qnames. The
        # innermost context is the last element; activity targets emitted
        # while the stack is non-empty get the chain appended.
        self._context_stack: List[str] = []

    # async helpers
    @property
    def _def(self) -> str:
        return "async def" if self._is_async else "def"

    @property
    def _await(self) -> str:
        return "await " if self._is_async else ""

    def _fresh(self, prefix: str) -> str:
        """Mint a unique local name for generated code so nested constructs
        never share a temporary. Without this, a `case` nested in a `case`
        reuses one scrutinee variable and a timeout loop nested in another
        reuses one `_deadline`, which lets the inner loop overwrite the
        outer loop's deadline (see the `_stmt_*` emitters below). Draws from
        the same counter as step/branch names so no two minted names collide.
        """
        self._step_counter += 1
        return f"{prefix}_{self._step_counter}"

    # ---- top-level ----
    def emit_procedure(self, proc: Tree) -> str:
        sections = proc.children  # already-flattened (rule is ?section)
        # categorize
        declare = main = preconds = watchdog = confirmation = None
        for s in sections:
            tag = s.data
            if tag == "declare_section":
                declare = s
            elif tag == "main_section":
                main = s
            elif tag == "preconditions_section":
                preconds = s
            elif tag == "watchdog_section":
                watchdog = s
            elif tag == "confirmation_section":
                confirmation = s

        if self._is_class:
            return self._emit_class(declare, watchdog, preconds, main, confirmation)
        return self._emit_functions(declare, watchdog, preconds, main, confirmation)

    def _emit_functions(self, declare, watchdog, preconds, main, confirmation) -> str:
        lines: List[str] = []
        lines.append(f"{self._def} main():")
        lines.append(f'{INDENT}proc = Procedure("transpiled")')
        lines.append(f"{INDENT}proc.start()")

        if declare is not None:
            lines.append(f"{INDENT}# --- declarations ---")
            for decl in declare.children:
                lines.append(INDENT + self._emit_event_decl(decl))

        if watchdog is not None:
            lines.append(f"{INDENT}# --- watchdog handlers ---")
            lines.extend(_indent_block(self._emit_watchdogs(watchdog), 1))

        if preconds is not None:
            lines.append(f"{INDENT}# --- preconditions ---")
            for pre in preconds.children:
                lines.extend(_indent_block(self._emit_statement(pre), 1))

        if main is not None:
            lines.append(f"{INDENT}# --- main ---")
            for stmt in main.children:
                lines.extend(_indent_block(self._emit_statement(stmt), 1))

        if confirmation is not None:
            lines.append(f"{INDENT}# --- confirmation ---")
            for stmt in confirmation.children:
                lines.extend(_indent_block(self._emit_statement(stmt), 1))

        lines.append(f"{INDENT}proc.finish()")
        return "\n".join(lines) + "\n"

    def _emit_class(self, declare, watchdog, preconds, main, confirmation) -> str:
        lines: List[str] = []
        lines.append("class TranspiledProcedure(Procedure):")
        lines.append(f"{INDENT}def __init__(self):")
        lines.append(f'{INDENT}{INDENT}super().__init__("transpiled")')

        if declare is not None:
            lines.append(f"{INDENT}{INDENT}# --- declarations ---")
            for decl in declare.children:
                lines.append(f"{INDENT}{INDENT}{self._emit_event_decl(decl)}")

        if watchdog is not None:
            lines.append(f"{INDENT}{INDENT}# --- watchdog handlers ---")
            lines.extend(_indent_block(self._emit_watchdogs(watchdog), 2))

        lines.append("")
        lines.append(f"{INDENT}{self._def} run(self):")
        lines.append(f"{INDENT}{INDENT}self.start()")

        if preconds is not None:
            lines.append(f"{INDENT}{INDENT}# --- preconditions ---")
            for pre in preconds.children:
                lines.extend(_indent_block(self._emit_statement(pre), 2))

        if main is not None:
            lines.append(f"{INDENT}{INDENT}# --- main ---")
            for stmt in main.children:
                lines.extend(_indent_block(self._emit_statement(stmt), 2))

        if confirmation is not None:
            lines.append(f"{INDENT}{INDENT}# --- confirmation ---")
            for stmt in confirmation.children:
                lines.extend(_indent_block(self._emit_statement(stmt), 2))

        lines.append(f"{INDENT}{INDENT}self.finish()")
        return "\n".join(lines) + "\n"

    def _emit_watchdogs(self, watchdog: Tree) -> List[str]:
        """Emit handler definitions and registrations. Returns lines at depth 0."""
        out: List[str] = []
        for handler in watchdog.children:
            ev_name = _text_of_name(handler.children[0])
            self._step_counter += 1
            fn_name = f"_watchdog_{self._step_counter}"
            out.append(f"{self._def} {fn_name}():")
            body = handler.children[1:]
            if not body:
                out.append(f"{INDENT}pass")
            else:
                for s in body:
                    out.extend(_indent_block(self._emit_statement(s), 1))
            out.append(f'{self._receiver}.register_watchdog("{ev_name}", {fn_name})')
        return out

    def _emit_event_decl(self, decl: Tree) -> str:
        name = _text_of_name(decl.children[0])
        if len(decl.children) > 1:
            desc = _text_of_description(decl.children[1])
            return f'{self._receiver}.declare_event(Event("{name}", description={desc!r}))'
        return f'{self._receiver}.declare_event(Event("{name}"))'

    # ---- statements: each returns a list of lines (no leading indent) ----
    def _emit_statement(self, stmt: Tree) -> List[str]:
        method = getattr(self, f"_stmt_{stmt.data}", None)
        if method is None:
            raise TranspileError(f"unsupported statement: {stmt.data}")
        return method(stmt)

    def _stmt_initiate_stmt(self, node: Tree) -> List[str]:
        call = self._emit_activity_call(node.children[0])
        instance = None
        for c in node.children[1:]:
            if isinstance(c, Tree) and c.data == "refer_by":
                instance = _text_of_name(c.children[0])
        if instance is None:
            return [f"initiate({self._receiver}, {call})"]
        return [f'initiate({self._receiver}, {call}, instance_name="{instance}")']

    def _stmt_initiate_confirm_stmt(self, node: Tree) -> List[str]:
        call = self._emit_activity_call(node.children[0])
        # Look for refer_by (between activity_call and continuation_test)
        instance = None
        for c in node.children[1:]:
            if isinstance(c, Tree) and c.data == "refer_by":
                instance = _text_of_name(c.children[0])
        ct = _continuation_test_node(node)
        if instance is not None:
            invocation = (
                f"{self._await}initiate_and_confirm("
                f'{self._receiver}, {call}, instance_name="{instance}")'
            )
        else:
            invocation = f"{self._await}initiate_and_confirm({self._receiver}, {call})"
        if ct is None:
            return [invocation]
        return self._emit_with_continuation(invocation, ct)

    def _stmt_initiate_confirm_step(self, node: Tree) -> List[str]:
        """Emit a step with full A.1.7-style structure.

        A step now mirrors a procedure: its body is a sequence of named
        sections (declare / preconditions / main / watchdog / confirmation),
        each emitted in the canonical order inside the step's body function.
        """
        step_name = _text_of_name(node.children[0])
        ct = _continuation_test_node(node)
        sections = [
            c for c in node.children[1:]
            if isinstance(c, Tree) and c.data.endswith("_section")
        ]
        section_map: dict[str, Tree] = {s.data: s for s in sections}

        self._step_counter += 1
        fn_name = f"_step_{self._step_counter}"
        lines = [f"{self._def} {fn_name}():"]

        body: List[str] = []
        if "declare_section" in section_map:
            body.append("# step declarations")
            for decl in section_map["declare_section"].children:
                body.append(self._emit_event_decl(decl))
        if "watchdog_section" in section_map:
            body.append("# step watchdog handlers")
            for handler in section_map["watchdog_section"].children:
                ev_name = _text_of_name(handler.children[0])
                self._step_counter += 1
                wd_fn = f"_step_watchdog_{self._step_counter}"
                body.append(f"{self._def} {wd_fn}():")
                hbody = handler.children[1:]
                if not hbody:
                    body.append(f"{INDENT}pass")
                else:
                    for s in hbody:
                        body.extend(_indent_block(self._emit_statement(s), 1))
                body.append(f'{self._receiver}.register_watchdog("{ev_name}", {wd_fn})')
        if "preconditions_section" in section_map:
            body.append("# step preconditions")
            for pre in section_map["preconditions_section"].children:
                body.extend(self._emit_statement(pre))
        if "main_section" in section_map:
            if any(k in section_map for k in ("declare_section", "preconditions_section", "watchdog_section")):
                body.append("# step main")
            for stmt in section_map["main_section"].children:
                body.extend(self._emit_statement(stmt))
        if "confirmation_section" in section_map:
            body.append("# step confirmation")
            for stmt in section_map["confirmation_section"].children:
                body.extend(self._emit_statement(stmt))

        if not body:
            body = ["pass"]
        lines.extend(_indent_block(body, 1))

        invocation = (
            f'{self._await}initiate_and_confirm_step('
            f'{self._receiver}, "{step_name}", {fn_name})'
        )
        if ct is None:
            lines.append(invocation)
            return lines
        lines.extend(self._emit_with_continuation(invocation, ct))
        return lines

    def _emit_with_continuation(self, invocation: str, ct: Tree) -> List[str]:
        """Wrap an `initiate and confirm` invocation in the retry/dispatch
        loop dictated by a `continuation_test` (PLUTO spec A.3.9.33).

        Emits a while-True loop that:
          * runs the invocation,
          * captures its confirmation status ("confirmed" / "not confirmed"
            / "aborted"),
          * dispatches on the user-specified arms (defaults per A.2.5),
          * implements restart [max N times | with timeout T] by `continue`,
          * implements abort/terminate by raising PlutoAborted/PlutoTerminated,
          * applies `continue` / `resume` by breaking out of the loop.
        """
        self._step_counter += 1
        n = self._step_counter
        cs = f"_cs_{n}"
        restarts = f"_restarts_{n}"
        deadline = f"_deadline_{n}"
        # Pre-compute arms keyed by normalized status.
        arms: dict[str, Tree] = {}
        for arm in ct.children:
            label_node, action_node = arm.children[0], arm.children[1]
            label_key = {
                "cs_confirmed": "confirmed",
                "cs_not_confirmed": "not confirmed",
                "cs_aborted": "aborted",
            }[label_node.data]
            arms[label_key] = action_node

        # If any arm uses `restart with timeout`, we need a deadline.
        needs_deadline = any(
            _restart_limit(a) and _restart_limit(a).data == "restart_timeout"
            for a in arms.values()
        )

        lines: List[str] = []
        lines.append(f"{restarts} = 0")
        if needs_deadline:
            lines.append(f"{deadline} = __import__('time').time() + 0  # set on first restart-with-timeout")
        lines.append("while True:")
        lines.append(f"{INDENT}try:")
        lines.append(f"{INDENT}{INDENT}{invocation}")
        lines.append(f'{INDENT}{INDENT}{cs} = "confirmed"')
        lines.append(f"{INDENT}except PlutoAborted:")
        lines.append(f'{INDENT}{INDENT}{cs} = "aborted"')
        lines.append(f"{INDENT}except PlutoTerminated:")
        lines.append(f"{INDENT}{INDENT}raise")
        lines.append(f"{INDENT}except Exception:")
        lines.append(f'{INDENT}{INDENT}{cs} = "not confirmed"')

        # Defaults per spec A.2.5: confirmed -> continue; otherwise -> abort.
        default_action = {
            "confirmed":     ("continue",),
            "not confirmed": ("abort",),
            "aborted":       ("abort",),
        }

        first = True
        for status in ("confirmed", "not confirmed", "aborted"):
            arm_action = arms.get(status)
            keyword = "if" if first else "elif"
            first = False
            lines.append(f'{INDENT}{keyword} {cs} == "{status}":')
            if arm_action is not None:
                lines.extend(_indent_block(
                    self._emit_action(arm_action, restarts, deadline), 2,
                ))
            else:
                lines.extend(_indent_block(
                    self._emit_default_action(default_action[status][0]), 2,
                ))
        lines.append(f"{INDENT}break  # fall-through")
        return lines

    def _emit_action(self, action: Tree, restarts: str, deadline: str) -> List[str]:
        kind = action.data
        if kind in ("act_resume", "act_continue"):
            return ["break"]
        if kind == "act_abort":
            return ['raise PlutoAborted("aborted by continuation test")']
        if kind == "act_terminate":
            return ['raise PlutoTerminated("terminated by continuation test")']
        if kind == "act_ask":
            return [
                '_resp = input("[ask user] action? (resume / abort / restart): ").strip().lower()',
                'if _resp == "abort": raise PlutoAborted("aborted by user")',
                'if _resp == "restart": continue',
                "break",
            ]
        if kind == "act_raise":
            ev = _text_of_name(action.children[0])
            call_raise = f'{self._await}{self._receiver}.raise_event("{ev}")'
            return [call_raise, "break"]
        if kind == "act_restart":
            limit = _restart_limit(action)
            if limit is None:
                return [f"{restarts} += 1", "continue"]
            if limit.data == "restart_max":
                bound = self._emit_expression(limit.children[0])
                return [
                    f"if {restarts} < {bound}:",
                    f"{INDENT}{restarts} += 1",
                    f"{INDENT}continue",
                    "break  # max restarts exhausted",
                ]
            if limit.data == "restart_timeout":
                t = self._emit_expression(limit.children[0])
                return [
                    f"if {restarts} == 0:",
                    f"{INDENT}{deadline} = __import__('time').time() + {t}",
                    f"if __import__('time').time() < {deadline}:",
                    f"{INDENT}{restarts} += 1",
                    f"{INDENT}continue",
                    "break  # restart deadline exceeded",
                ]
        raise TranspileError(f"unsupported continuation action: {kind}")

    def _emit_default_action(self, kind: str) -> List[str]:
        if kind == "continue":
            return ["break"]
        if kind == "abort":
            return ['raise PlutoAborted("aborted (default action)")']
        raise TranspileError(f"bad default action: {kind}")

    def _stmt_parallel_all_stmt(self, node: Tree) -> List[str]:
        return self._emit_parallel(node, "parallel_until_all")

    def _stmt_parallel_one_stmt(self, node: Tree) -> List[str]:
        return self._emit_parallel(node, "parallel_until_one")

    def _emit_parallel(self, node: Tree, fn: str) -> List[str]:
        lines: List[str] = []
        branch_names: List[str] = []
        for branch in node.children:
            self._step_counter += 1
            branch_fn = f"_branch_{self._step_counter}"
            lines.append(f"{self._def} {branch_fn}():")
            inner = self._emit_statement(branch)
            for ln in _indent_block(inner, 1):
                lines.append(ln)
            branch_names.append(branch_fn)
        lines.append(f"{self._await}{fn}([{', '.join(branch_names)}])")
        return lines

    def _stmt_context_stmt(self, node: Tree) -> List[str]:
        """Emit a context block (PLUTO spec A.3.9.10).

        Activity calls inside the block have the context appended to
        their target qualifier: `Switch on Y` inside
        `in the context of X do … end context` is emitted as
        `switch_on("Y of X")`. Contexts nest; the innermost is the most
        immediate qualifier.
        """
        target = _text_of_qname(node.children[0])
        body = node.children[1:]
        lines = [f"# in the context of {target}"]
        self._context_stack.append(target)
        try:
            for s in body:
                lines.extend(self._emit_statement(s))
        finally:
            self._context_stack.pop()
        lines.append("# end context")
        return lines

    def _apply_context(self, target: str) -> str:
        """Append the active context chain to an activity target."""
        if not self._context_stack:
            return target
        chain = " of ".join(reversed(self._context_stack))
        return f"{target} of {chain}"

    def _stmt_if_stmt(self, node: Tree) -> List[str]:
        expr = self._emit_expression(node.children[0])
        then_node = node.children[1]  # if_then
        else_node = node.children[2] if len(node.children) > 2 else None  # if_else
        lines = [f"if {expr}:"]
        for s in then_node.children:
            lines.extend(_indent_block(self._emit_statement(s), 1))
        if else_node is not None:
            lines.append("else:")
            for s in else_node.children:
                lines.extend(_indent_block(self._emit_statement(s), 1))
        return lines

    def _stmt_case_stmt(self, node: Tree) -> List[str]:
        expr = self._emit_expression(node.children[0])
        var = self._fresh("_case_expr")
        lines = [f"{var} = {expr}"]
        first = True
        otherwise_node = None
        for child in node.children[1:]:
            if child.data == "case_otherwise":
                otherwise_node = child
                continue
            arm_expr = self._emit_expression(child.children[0])
            stmts = child.children[1:]
            keyword = "if" if first else "elif"
            first = False
            lines.append(f"{keyword} {var} == {arm_expr}:")
            if not stmts:
                lines.append(f"{INDENT}pass")
            for s in stmts:
                lines.extend(_indent_block(self._emit_statement(s), 1))
        if otherwise_node is not None:
            lines.append("else:")
            for s in otherwise_node.children:
                lines.extend(_indent_block(self._emit_statement(s), 1))
        return lines

    def _stmt_while_stmt(self, node: Tree) -> List[str]:
        expr = self._emit_expression(node.children[0])
        timeout = self._extract_timeout(node)
        body = [c for c in node.children[1:] if not (isinstance(c, Tree) and c.data == "timeout_clause")]
        if timeout:
            # convert `while EXPR do BODY with timeout T` into a time-limited while loop
            deadline = self._fresh("_deadline")
            lines = [
                f"{deadline} = __import__('time').time() + {timeout}",
                f"while ({expr}) and __import__('time').time() < {deadline}:",
            ]
        else:
            lines = [f"while {expr}:"]
        if not body:
            lines.append(f"{INDENT}pass")
        else:
            for s in body:
                lines.extend(_indent_block(self._emit_statement(s), 1))
        return lines

    def _stmt_for_stmt(self, node: Tree) -> List[str]:
        var_name = _text_of_name(node.children[0])
        # children: name, expr, expr, (expr by)?, statements...
        rest = node.children[1:]
        start_expr = self._emit_expression(rest[0])
        end_expr = self._emit_expression(rest[1])
        # remaining children: optionally a 'by' expr (Tree), then statements
        idx = 2
        step = "1"
        if idx < len(rest) and _is_expression(rest[idx]):
            step = self._emit_expression(rest[idx])
            idx += 1
        body = rest[idx:]
        py_var = _py_ident(var_name)
        lines = [f"for {py_var} in range(int({start_expr}), int({end_expr}) + 1, int({step})):"]
        if not body:
            lines.append(f"{INDENT}pass")
        else:
            for s in body:
                lines.extend(_indent_block(self._emit_statement(s), 1))
        return lines

    def _stmt_repeat_stmt(self, node: Tree) -> List[str]:
        timeout = self._extract_timeout(node)
        non_timeout = [c for c in node.children if not (isinstance(c, Tree) and c.data == "timeout_clause")]
        body = non_timeout[:-1]
        cond = self._emit_expression(non_timeout[-1])
        lines = []
        deadline = self._fresh("_deadline") if timeout else None
        if timeout:
            lines.append(f"{deadline} = __import__('time').time() + {timeout}")
        lines.append("while True:")
        if not body:
            lines.append(f"{INDENT}pass")
        else:
            for s in body:
                lines.extend(_indent_block(self._emit_statement(s), 1))
        lines.append(f"{INDENT}if {cond}:")
        lines.append(f"{INDENT}{INDENT}break")
        if timeout:
            lines.append(f"{INDENT}if __import__('time').time() >= {deadline}:")
            lines.append(f"{INDENT}{INDENT}break")
        return lines

    def _stmt_wait_for_event(self, node: Tree) -> List[str]:
        ev = _text_of_name(node.children[0])
        timeout = self._extract_timeout(node)
        timeout_arg = f", timeout={timeout}" if timeout else ""
        return [f'{self._await}wait_for_event({self._receiver}, "{ev}"{timeout_arg})']

    def _stmt_wait_until_expr(self, node: Tree) -> List[str]:
        expr = self._emit_expression(node.children[0])
        timeout = self._extract_timeout(node)
        timeout_arg = f", timeout={timeout}" if timeout else ""
        return [f"{self._await}wait_until(lambda: {expr}{timeout_arg})"]

    def _extract_timeout(self, node: Tree) -> str | None:
        for child in node.children:
            if isinstance(child, Tree) and child.data == "timeout_clause":
                return self._emit_expression(child.children[0])
        return None

    def _stmt_assign_stmt(self, node: Tree) -> List[str]:
        var = _py_ident(_text_of_name(node.children[0]))
        expr = self._emit_expression(node.children[1])
        return [f"{var} = {expr}", f'{self._receiver}.variables["{var}"] = {var}']

    def _stmt_log_stmt(self, node: Tree) -> List[str]:
        expr = self._emit_expression(node.children[0])
        return [f"pluto_log({expr})"]

    def _stmt_inform_stmt(self, node: Tree) -> List[str]:
        expr = self._emit_expression(node.children[0])
        return [f"inform_user({expr})"]

    def _stmt_raise_stmt(self, node: Tree) -> List[str]:
        ev = _text_of_name(node.children[0])
        return [f'{self._await}{self._receiver}.raise_event("{ev}")']

    def _stmt_save_context_stmt(self, node: Tree) -> List[str]:
        items = []
        for entry in node.children:
            ref = _text_of_qname(entry.children[0])
            local = _text_of_name(entry.children[1])
            items.append(f'("{ref}", "{local}")')
        return [f"{self._receiver}.save_context([{', '.join(items)}])"]

    # ---- activity calls ----
    def _emit_activity_call(self, node: Tree) -> str:
        if node.data == "switch_on":
            target = self._apply_context(_text_of_qname(node.children[0]))
            args_kw = self._emit_activity_args(node.children[1:])
            return f'switch_on("{target}"{args_kw})'
        if node.data == "switch_off":
            target = self._apply_context(_text_of_qname(node.children[0]))
            args_kw = self._emit_activity_args(node.children[1:])
            return f'switch_off("{target}"{args_kw})'
        raise TranspileError(f"unsupported activity: {node.data}")

    def _emit_activity_args(self, tail_children) -> str:
        """Emit `, arguments={…}` for an `activity_with` clause.

        Supports simple / record / array argument shapes (spec A.3.9.28).
        A record arg becomes a nested dict; an array arg becomes a list.
        """
        for c in tail_children:
            if isinstance(c, Tree) and c.data == "activity_with":
                items = []
                for arg in c.children:
                    name = _text_of_name(arg.children[0])
                    if arg.data == "simple_arg":
                        value = self._emit_expression(arg.children[1])
                        items.append(f'"{name}": {value}')
                    elif arg.data == "record_arg":
                        rec_parts = []
                        for sub in arg.children[1:]:
                            sub_name = _text_of_name(sub.children[0])
                            sub_value = self._emit_expression(sub.children[1])
                            rec_parts.append(f'"{sub_name}": {sub_value}')
                        items.append(f'"{name}": {{{", ".join(rec_parts)}}}')
                    elif arg.data == "array_arg":
                        elements = [self._emit_expression(e) for e in arg.children[1:]]
                        items.append(f'"{name}": [{", ".join(elements)}]')
                    else:
                        raise TranspileError(f"unsupported argument shape: {arg.data}")
                if not items:
                    return ""
                return f', arguments={{{", ".join(items)}}}'
        return ""

    # ---- expressions ----
    def _emit_expression(self, node) -> str:
        if isinstance(node, Token):
            return _emit_token(node)
        d = node.data
        if d == "num_lit":
            return str(node.children[0])
        if d == "str_lit":
            return str(node.children[0])
        if d == "prop_req":
            return self._emit_property_request(node.children[0])
        if d == "var_ref":
            qn = _text_of_qname(node.children[0])
            return _ref_to_python(qn, self._receiver)
        if d == "not_op":
            return f"(not {self._emit_expression(node.children[0])})"
        if d == "or_expr":
            return "(" + " or ".join(self._emit_expression(c) for c in node.children) + ")"
        if d == "and_expr":
            return "(" + " and ".join(self._emit_expression(c) for c in node.children) + ")"
        if d == "comparison":
            # children: arith, CMP_OP, arith
            left = self._emit_expression(node.children[0])
            op = _map_cmp_op(str(node.children[1]))
            right = self._emit_expression(node.children[2])
            return f"({left} {op} {right})"
        if d == "between_expr":
            # `X between LO and HI` -> LO <= X <= HI (inclusive, spec A.3.9.34)
            x = self._emit_expression(node.children[0])
            lo = self._emit_expression(node.children[1])
            hi = self._emit_expression(node.children[2])
            return f"({lo} <= {x} <= {hi})"
        if d == "within_expr":
            # `X within TOL of Y` -> abs(X - Y) <= TOL
            x = self._emit_expression(node.children[0])
            tol = self._emit_expression(node.children[1])
            y = self._emit_expression(node.children[2])
            return f"(abs({x} - {y}) <= {tol})"
        if d == "within_pct_expr":
            # `X within TOL % of Y` -> abs(X - Y) <= abs(Y) * TOL / 100
            x = self._emit_expression(node.children[0])
            tol = self._emit_expression(node.children[1])
            y = self._emit_expression(node.children[2])
            return f"(abs({x} - {y}) <= abs({y}) * ({tol}) / 100)"
        if d == "in_expr":
            # `X in (A, B, ...)` -> X in (A, B, ..., )  (trailing comma forces a tuple)
            x = self._emit_expression(node.children[0])
            elems = ", ".join(self._emit_expression(c) for c in node.children[1:])
            return f"({x} in ({elems},))"
        if d == "arith":
            return _emit_binop_chain(node.children, self._emit_expression)
        if d == "term":
            return _emit_binop_chain(node.children, self._emit_expression)
        if d == "qname":
            qn = _text_of_qname(node)
            return _ref_to_python(qn, self._receiver)
        raise TranspileError(f"unsupported expression: {d}")

    def _emit_property_request(self, node) -> str:
        """Emit code to get an activity property.

        Translates: execution_status of CHECK_TRACKER
        To: proc.get_property("CHECK_TRACKER", "execution_status")

        `node` is the property_request tree with two children:
        a `property_name` tree (children are WORD tokens) and a `qname`.
        """
        prop_name_node = node.children[0]
        qname_node = node.children[1]
        prop_name = _text_of_name(prop_name_node)
        activity_name = _text_of_qname(qname_node)
        return f'{self._receiver}.get_property("{activity_name}", "{prop_name}")'


def _is_continuation_test(c) -> bool:
    return isinstance(c, Tree) and c.data == "continuation_test"


def _continuation_test_node(node: Tree) -> Tree | None:
    for c in node.children:
        if _is_continuation_test(c):
            return c
    return None


def _restart_limit(action: Tree) -> Tree | None:
    if action.data != "act_restart":
        return None
    for c in action.children:
        if isinstance(c, Tree) and c.data in ("restart_max", "restart_timeout"):
            return c
    return None


def _emit_binop_chain(children, emit) -> str:
    out = emit(children[0])
    i = 1
    while i < len(children):
        op = str(children[i])
        right = emit(children[i + 1])
        out = f"({out} {op} {right})"
        i += 2
    return out


def _emit_token(tok: Token) -> str:
    if tok.type == "NUMBER":
        return str(tok)
    if tok.type == "ESCAPED_STRING":
        return str(tok)
    if tok.type == "WORD":
        return _py_ident(str(tok))
    return str(tok)


def _map_cmp_op(op: str) -> str:
    return {"=": "==", "<>": "!="}.get(op, op)


def _is_expression(node) -> bool:
    if isinstance(node, Token):
        return False
    return node.data in {
        "or_expr", "and_expr", "not_op", "comparison",
        "between_expr", "within_expr", "within_pct_expr", "in_expr",
        "arith", "term", "num_lit", "str_lit", "var_ref", "qname", "prop_req",
    }


def _ref_to_python(qn: str, receiver: str) -> str:
    """Resolve any PLUTO reference at runtime via `Procedure.resolve_ref`.

    `resolve_ref` walks local variables -> local reporting-data snapshots
    -> the global reporting-data registry, matching the spec A.3.9.8
    precedence. Falls back to the name itself as a string when nothing
    matches (preserving the previous behaviour for unknown names).
    """
    return f'{receiver}.resolve_ref({qn!r})'


def _py_ident(name: str) -> str:
    safe = name.replace(" ", "_").replace("-", "_")
    if not safe or not (safe[0].isalpha() or safe[0] == "_"):
        safe = "_" + safe
    return safe


def _indent_block(lines: List[str], depth: int) -> List[str]:
    pad = INDENT * depth
    return [pad + ln for ln in lines]
