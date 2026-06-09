"""Lark parser wrapper. Loads grammar.lark and exposes parse() with
friendly error messages on parse failure."""
from __future__ import annotations

import pathlib
from functools import lru_cache
from typing import Iterable

from lark import Lark, Tree
from lark.exceptions import (
    UnexpectedCharacters,
    UnexpectedEOF,
    UnexpectedInput,
    UnexpectedToken,
)


_GRAMMAR_PATH = pathlib.Path(__file__).parent / "grammar.lark"


# Anonymous Lark token names look like "__ANON_0"; map them back to literals
# where we can guess. Stripped from suggestions when we can't.
_ANON_PREFIX = "__ANON_"


class PlutoParseError(Exception):
    """A friendly parse error with source location and a caret marker."""

    def __init__(self, message: str, *, line: int | None = None, column: int | None = None,
                 filename: str | None = None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column
        self.filename = filename

    def __str__(self) -> str:
        return self.message


@lru_cache(maxsize=1)
def _parser() -> Lark:
    return Lark(
        _GRAMMAR_PATH.read_text(),
        start="start",
        parser="earley",
        propagate_positions=True,
    )


def parse(source: str, *, filename: str | None = None) -> Tree:
    """Parse PLUTO source. On failure, raises PlutoParseError with friendly context."""
    try:
        return _parser().parse(source)
    except UnexpectedInput as e:
        raise _build_friendly_error(e, source, filename) from None


def _build_friendly_error(
    e: UnexpectedInput,
    source: str,
    filename: str | None,
) -> PlutoParseError:
    lines = source.splitlines() or [""]
    raw_line = getattr(e, "line", None)
    raw_col = getattr(e, "column", None)

    if isinstance(e, UnexpectedEOF) or raw_line in (None, -1):
        # point at the last non-empty line of the source
        line = len(lines)
        while line > 1 and not lines[line - 1].strip():
            line -= 1
        column = len(lines[line - 1]) + 1 if lines else 1
    else:
        line = raw_line
        column = raw_col or 1

    src_line = lines[line - 1] if 0 < line <= len(lines) else ""
    caret = " " * max(column - 1, 0) + "^"

    header_loc = f"{filename}:{line}:{column}" if filename else f"line {line}, column {column}"

    if isinstance(e, UnexpectedToken):
        got = _describe_token(e.token)
        expected = _describe_expected(e.expected)
        msg = f"at {header_loc}: unexpected {got}; expected {expected}"
    elif isinstance(e, UnexpectedCharacters):
        allowed = getattr(e, "allowed", None) or ()
        expected = _describe_expected(allowed)
        ch = src_line[column - 1] if 0 < column <= len(src_line) else "?"
        msg = f"at {header_loc}: cannot start a token with {ch!r}; expected {expected}"
    elif isinstance(e, UnexpectedEOF):
        expected = _describe_expected(getattr(e, "expected", ()))
        msg = f"at {header_loc}: unexpected end of file; expected {expected}"
    else:
        msg = f"at {header_loc}: {e}"

    hint = _hint_for(e, src_line, column)
    body_lines = [
        msg,
        "",
        f"  {line:>4} | {src_line}",
        f"       | {caret}",
    ]
    if hint:
        body_lines += ["", f"hint: {hint}"]

    return PlutoParseError("\n".join(body_lines), line=line, column=column, filename=filename)


def _describe_token(tok) -> str:
    if tok is None:
        return "input"
    raw = getattr(tok, "value", str(tok))
    return f"{raw!r}"


def _describe_expected(names: Iterable[str]) -> str:
    items = sorted({_pretty_name(n) for n in names if n})
    items = [i for i in items if i]
    if not items:
        return "something else"
    if len(items) == 1:
        return items[0]
    if len(items) <= 4:
        return ", ".join(items[:-1]) + f", or {items[-1]}"
    return ", ".join(items[:3]) + f", or one of {len(items) - 3} more"


def _pretty_name(name: str) -> str:
    if name.startswith(_ANON_PREFIX):
        return ""
    table = {
        "WORD": "an identifier",
        "NUMBER": "a number",
        "ESCAPED_STRING": "a quoted string",
        "CMP_OP": "a comparison operator",
        "ADD_OP": "+ or -",
        "MUL_OP": "* or /",
    }
    if name in table:
        return table[name]
    # Anonymous keyword tokens often have form like "PROCEDURE" / "END" — uppercase the keyword
    return repr(name.lower())


# Common-typo / structure hints
_COMMON_HINTS = [
    ("end procedure", "did you forget a closing 'end <section>' before 'end procedure'?"),
    ("end main", "every 'main' section must be terminated with 'end main'"),
    ("end declare", "every 'declare' section must be terminated with 'end declare'"),
    ("end if", "an 'if … then …' must be terminated with 'end if'"),
    ("end step", "an 'initiate and confirm step' block ends with 'end step'"),
    ("end parallel", "an 'in parallel until …' block ends with 'end parallel'"),
]


def _hint_for(e: UnexpectedInput, src_line: str, column: int) -> str | None:
    text = src_line.strip().lower()
    if isinstance(e, UnexpectedEOF):
        return "the procedure may be missing a closing 'end procedure'"
    if "end" in text:
        return "check that the matching opening keyword (procedure / main / declare / if / parallel) exists"
    if text.startswith("if ") and "then" not in text:
        return "an if statement looks like: if EXPR then STATEMENTS end if"
    if text.startswith("initiate") and "switch" not in text and "step" not in text:
        return "expected an activity call after 'initiate' (e.g. 'Switch on Star Tracker1')"
    return None
