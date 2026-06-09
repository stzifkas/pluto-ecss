"""Command-line interface for pluto_ecss.

Usage:
    pluto-ecss parse    SCRIPT          # show parse tree
    pluto-ecss compile  SCRIPT [-o OUT] # emit Python source
    pluto-ecss run      SCRIPT          # transpile and execute
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import tempfile

from pluto_ecss import __version__
from pluto_ecss.parser import parse as parse_pluto, PlutoParseError
from pluto_ecss.transpiler import transpile


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pluto-ecss", description="PLUTO -> Python transpiler")
    ap.add_argument("--version", action="version", version=f"pluto-ecss {__version__}")
    ap.add_argument("-v", "--verbose", action="store_true", help="emit runtime lifecycle logs")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_parse = sub.add_parser("parse", help="show the parse tree of a PLUTO script")
    p_parse.add_argument("script", type=pathlib.Path)

    p_compile = sub.add_parser("compile", help="transpile to Python")
    p_compile.add_argument("script", type=pathlib.Path)
    p_compile.add_argument("-o", "--output", type=pathlib.Path, help="output .py path (default: stdout)")
    p_compile.add_argument("--runtime", choices=("threaded", "async"), default="threaded",
                           help="target runtime (default: threaded)")
    p_compile.add_argument("--style", choices=("functions", "class"), default="functions",
                           help="emit a free main() function or a Procedure subclass (default: functions)")
    p_compile.add_argument("--emit", choices=("python", "json"), default="python",
                           help="output format (default: python)")
    p_compile.add_argument("--no-runtime", action="store_true",
                           help="inline the runtime so the output is self-contained (Python only)")

    p_run = sub.add_parser("run", help="transpile and execute")
    p_run.add_argument("script", type=pathlib.Path)
    p_run.add_argument("--keep", action="store_true", help="keep transpiled .py file and print its path")
    p_run.add_argument("--runtime", choices=("threaded", "async"), default="threaded",
                       help="target runtime (default: threaded)")
    p_run.add_argument("--style", choices=("functions", "class"), default="functions",
                       help="emit a free main() function or a Procedure subclass (default: functions)")

    p_demo = sub.add_parser("demo", help="live TUI dashboard (requires rich)")
    p_demo.add_argument("script", type=pathlib.Path, nargs="?", help="optional .pluto script; defaults to examples/05_full_bringup.pluto")

    p_fmt = sub.add_parser("fmt", help="canonicalise PLUTO source (pretty-print)")
    p_fmt.add_argument("script", type=pathlib.Path)
    p_fmt.add_argument("-i", "--in-place", action="store_true", help="rewrite the file in place")
    p_fmt.add_argument("--check", action="store_true", help="exit non-zero if the file isn't already canonical")

    p_gen = sub.add_parser("gen", help="generate a PLUTO procedure from a YAML spec")
    p_gen.add_argument("spec", type=pathlib.Path, help="YAML spec file")
    p_gen.add_argument("-o", "--output", type=pathlib.Path, help="output .pluto path (default: stdout)")

    args = ap.parse_args(argv)

    if args.verbose:
        import logging
        logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    if args.cmd == "parse":
        try:
            tree = parse_pluto(args.script.read_text(), filename=str(args.script))
        except PlutoParseError as e:
            print(f"pluto-ecss: parse error\n{e}", file=sys.stderr)
            return 1
        print(tree.pretty())
        return 0

    if args.cmd == "compile":
        try:
            if args.emit == "json":
                from pluto_ecss.json_emit import transpile_to_json
                out = transpile_to_json(args.script.read_text(), filename=str(args.script))
                if not out.endswith("\n"):
                    out += "\n"
            else:
                out = transpile(
                    args.script.read_text(),
                    module_doc=f"Transpiled from {args.script.name}",
                    runtime=args.runtime,
                    style=args.style,
                    no_runtime=args.no_runtime,
                )
        except PlutoParseError as e:
            print(f"pluto-ecss: parse error\n{e}", file=sys.stderr)
            return 1
        if args.output:
            args.output.write_text(out)
        else:
            sys.stdout.write(out)
        return 0

    if args.cmd == "demo":
        from pluto_ecss.demo import run_demo
        return run_demo(args.script)

    if args.cmd == "gen":
        from pluto_ecss.generator import generate_from_file, GeneratorError
        try:
            out = generate_from_file(args.spec)
        except (GeneratorError, PlutoParseError) as e:
            print(f"pluto-ecss: gen error\n{e}", file=sys.stderr)
            return 1
        if args.output:
            args.output.write_text(out)
        else:
            sys.stdout.write(out)
        return 0

    if args.cmd == "fmt":
        from pluto_ecss.formatter import format_source
        original = args.script.read_text()
        try:
            formatted = format_source(original, filename=str(args.script))
        except PlutoParseError as e:
            print(f"pluto-ecss: parse error\n{e}", file=sys.stderr)
            return 1
        if args.check:
            return 0 if original == formatted else 1
        if args.in_place:
            args.script.write_text(formatted)
            return 0
        sys.stdout.write(formatted)
        return 0

    if args.cmd == "run":
        try:
            py = transpile(
                args.script.read_text(),
                module_doc=f"Transpiled from {args.script.name}",
                runtime=args.runtime,
                style=args.style,
            )
        except PlutoParseError as e:
            print(f"pluto-ecss: parse error\n{e}", file=sys.stderr)
            return 1
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tmp:
            tmp.write(py)
            tmp_path = tmp.name
        if args.keep:
            print(f"[transpiled to {tmp_path}]", file=sys.stderr)
        ns: dict = {"__name__": "__main__", "__file__": tmp_path}
        try:
            exec(compile(py, tmp_path, "exec"), ns)
        finally:
            if not args.keep:
                os.unlink(tmp_path)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
