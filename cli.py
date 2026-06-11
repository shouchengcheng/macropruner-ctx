"""Command-line interface for MacroPruner-Ctx.

Three modes:
  1. `python3 -m macropruner read <file> [--target X] [--cdb Y] [--backend regex|clang]`
     - Print the pruned code to stdout (banner + body).
     - This is what you'd pipe to a file or another tool.

  2. `python3 -m macropruner skeleton <file> [--target X] [--cdb Y]`
     - Print the skeletonized version.

  3. `python3 -m macropruner diff <file> [--cdb Y]`
     - Run both regex and clang backends, print a unified diff between
       them. Useful for sanity-checking a project after upgrading
       MacroPruner-Ctx.

Configuration via .macroprunerrc is honored; the --target / --cdb
flags override it.

Exit codes:
  0  success
  1  fatal error (file not found, malformed diff, etc.)
  2  warnings only (e.g. unbalanced #if in the source)
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from config import load as load_config, resolve_compile_db


def _resolve_args(args: argparse.Namespace) -> tuple:
    """Merge CLI flags with .macroprunerrc defaults.

    Returns: (target, compile_db, mode, backend)
    """
    cfg = load_config()
    target = args.target or cfg.get("pruner.default_target", "") or "DEFAULT"
    backend = getattr(args, "backend", None) or cfg.get("pruner.default_backend", "regex")
    mode = getattr(args, "mode", None) or cfg.get("pruner.default_mode", "physical")
    if getattr(args, "cdb", ""):
        compile_db = args.cdb
    else:
        # Resolve relative to the directory of the file we're acting
        # on, not cwd. Otherwise `macropruner read src/main.c` from
        # any working dir would always pick up the project root's
        # cdb (if any) instead of the file's.
        file_dir = os.path.dirname(os.path.abspath(args.file)) if getattr(args, "file", None) else os.getcwd()
        resolved = resolve_compile_db(cfg, project_root=file_dir)
        compile_db = resolved or ""
    return target, compile_db, mode, backend


def _prune(args: argparse.Namespace, skeletonize: bool = False) -> int:
    from backends import get_backend

    target, compile_db, mode, backend = _resolve_args(args)
    if not compile_db:
        print(
            "[FATAL] No compile_commands.json found. Pass --cdb, drop a "
            ".macroprunerrc, or run from a directory with one.",
            file=sys.stderr,
        )
        return 1
    try:
        inst = get_backend(backend)
    except (ValueError, RuntimeError) as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        return 1

    try:
        result = inst.prune(args.file, target, compile_db, mode=mode)
    except FileNotFoundError as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[FATAL] {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if skeletonize:
        from skeletonizer import Skeletonizer
        skel = Skeletonizer()
        body = skel.skeletonize(result.code)
        stats = skel.get_stats()
        print(f"/* --- MacroPruner-Ctx (CLI / Skeleton) --------- */")
        print(f"/* Original:  {result.original_lines} lines                            */")
        print(f"/* Skeleton:  {stats['skeleton_lines']} lines                           */")
        print(f"/* Stripped:  {stats['functions_stripped']} functions                      */")
        print(f"/* ------------------------------------------------ */")
        print()
        print(body)
    else:
        if result.backend_name == "clang":
            # clang backend already printed its own banner
            print(result.code)
        else:
            tok = result.token_estimate
            print(f"/* --- MacroPruner-Ctx (CLI) --------------------- */")
            print(f"/* File:      {args.file:<40} */")
            print(f"/* Target:    {target:<40} */")
            print(f"/* Lines:     {result.original_lines - result.pruned_lines}/{result.original_lines} dropped ({result.reduction_percentage}%)       */")
            print(f"/* Tokens:    {tok['saved_tokens']}/{tok['original_tokens']} saved ({tok['saved_pct']}%)         */")
            print(f"/* Mode:      {mode:<40} */")
            print(f"/* Backend:   {result.backend_name:<40} */")
            print(f"/* ------------------------------------------------ */")
            print()
            print(result.code)
    return 0


def _diff(args: argparse.Namespace) -> int:
    """Print unified diff between regex and clang backends."""
    import difflib

    target, compile_db, mode, _ = _resolve_args(args)
    if not compile_db:
        print(
            "[FATAL] No compile_commands.json found. Pass --cdb or configure.",
            file=sys.stderr,
        )
        return 1

    from backends import get_backend

    try:
        regex_result = get_backend("regex").prune(args.file, target, compile_db, mode=mode)
        clang_inst = get_backend("clang")
        if not clang_inst.is_available()[0]:
            print("[FATAL] clang backend not available; cannot run diff", file=sys.stderr)
            return 1
        clang_result = clang_inst.prune(args.file, target, compile_db, mode=mode)
    except Exception as e:
        print(f"[FATAL] {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    # clang backend output is fully preprocessed; comparing raw
    # against regex's structurally-pruned output isn't directly
    # meaningful. Instead, compare the *set of original lines each
    # backend considers active*. Both backends report
    # `skipped_ranges` — the lines in the original file that
    # the backend dropped. The diff is meaningful when those sets
    # match: if regex thinks line 7 is inactive but clang thinks
    # it's active, the regex backend has a bug.
    def active_set_from_ranges(ranges, total_lines):
        active = set(range(1, total_lines + 1))
        for s, e in ranges:
            active -= set(range(s, e + 1))
        return active

    regex_active = active_set_from_ranges(
        regex_result.skipped_ranges, regex_result.original_lines
    )
    clang_active = active_set_from_ranges(
        clang_result.skipped_ranges, clang_result.original_lines
    )

    only_in_regex = regex_active - clang_active
    only_in_clang = clang_active - regex_active

    if not only_in_regex and not only_in_clang:
        print(
            f"OK: regex and clang agree on all {regex_result.original_lines} "
            f"lines being active or inactive."
        )
        return 0

    print(
        f"Disagreement: {len(only_in_regex)} line(s) only regex-active, "
        f"{len(only_in_clang)} line(s) only clang-active."
    )
    if only_in_regex:
        sample = sorted(only_in_regex)[:20]
        print(f"  regex active, clang inactive: {sample}{'...' if len(only_in_regex) > 20 else ''}")
    if only_in_clang:
        sample = sorted(only_in_clang)[:20]
        print(f"  clang active, regex inactive: {sample}{'...' if len(only_in_clang) > 20 else ''}")
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="macropruner",
        description="Macro-aware C/C++ code pruner. CLI for the MacroPruner-Ctx MCP server.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_read = sub.add_parser("read", help="Prune a single C/C++ file to stdout")
    p_read.add_argument("file")
    p_read.add_argument("--target", default="")
    p_read.add_argument("--cdb", default="", help="Path to compile_commands.json")
    p_read.add_argument("--mode", choices=["physical", "virtual"], default="")
    p_read.add_argument("--backend", choices=["regex", "clang", "auto"], default="")

    p_skel = sub.add_parser("skeleton", help="Prune + skeletonize")
    p_skel.add_argument("file")
    p_skel.add_argument("--target", default="")
    p_skel.add_argument("--cdb", default="")
    p_skel.add_argument("--mode", choices=["physical", "virtual"], default="")
    p_skel.add_argument("--backend", choices=["regex", "clang", "auto"], default="")

    p_diff = sub.add_parser(
        "diff", help="Compare regex vs clang backends on the same file"
    )
    p_diff.add_argument("file")
    p_diff.add_argument("--target", default="")
    p_diff.add_argument("--cdb", default="")
    p_diff.add_argument("--mode", choices=["physical", "virtual"], default="")

    args = parser.parse_args(argv)
    if args.cmd == "read":
        return _prune(args, skeletonize=False)
    if args.cmd == "skeleton":
        return _prune(args, skeletonize=True)
    if args.cmd == "diff":
        return _diff(args)
    return 1  # unreachable


if __name__ == "__main__":
    sys.exit(main())
