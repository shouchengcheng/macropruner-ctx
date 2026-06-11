"""
MacroPruner-Ctx MCP Server

Exposes:
  - Tool: read_c(file_path, target) → pruned source code with inactive conditional blocks removed
  - Resource: cfile:///path/to/file → read file (optionally pruned if target parameter provided)
"""

import os
import re
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from pruner_core import PrunerCore, PrunerMode
from cc_parser import CompileDBParser


server = FastMCP(
    "macropruner-ctx",
    instructions="Macro-aware C/C++ code pruner. Strips inactive conditional compilation "
    "blocks (#ifdef/#ifndef/#else/#elif/#endif) to optimize code for LLM context. "
    "Use read_c tool with a target macro name to get pruned source code.",
    host="127.0.0.1",
    port=8099,
)


def _get_active_macros_for_target(target: str) -> dict:
    """Build a simple macro dict from a target/product name string."""
    return {
        target.upper(): None,
        f"TARGET_{target.upper()}": None,
        f"PRODUCT_{target.upper()}": None,
    }


def _resolve_file_path(file_path: str) -> Optional[str]:
    """Resolve a file path, checking relative to CWD and absolute."""
    if os.path.isabs(file_path):
        if os.path.exists(file_path):
            return file_path
        return None
    cwd_path = os.path.join(os.getcwd(), file_path)
    if os.path.exists(cwd_path):
        return cwd_path
    return None


def _prune_file(
    file_path: str,
    target: str,
    compile_db: str,
    mode: PrunerMode = PrunerMode.PHYSICAL_DELETION,
) -> str:
    """Read a C/C++ file, extract macros from compile DB, prune inactive code."""
    resolved = _resolve_file_path(file_path)
    if not resolved:
        raise FileNotFoundError(f"Cannot resolve file path: {file_path}")

    if not os.path.isfile(compile_db):
        raise FileNotFoundError(f"compile_commands.json not found: {compile_db}")

    with open(resolved, "r") as f:
        source = f.read()

    active_macros = _get_active_macros_for_target(target)

    try:
        parser = CompileDBParser(compile_db)
        db_macros = parser.extract_macros(resolved)
        active_macros.update(db_macros)
    except json.JSONDecodeError:
        pass

    pruner = PrunerCore(active_macros=active_macros, mode=mode)
    return pruner.prune(source)


# ── Tool: read_c ──────────────────────────────────────────────


@server.tool(
    name="read_c",
    description="Read a C/C++ source file with inactive conditional compilation blocks "
    "(#ifdef, #ifndef, #else, #elif, #endif) pruned away. "
    "Specify the target product/macro to keep active branches for. "
    "Returns only the active code paths with a pruning summary.",
)
def read_c(
    file_path: str,
    target: str,
    compile_db: str,
    mode: str = "physical",
) -> str:
    """Read and prune a C/C++ source file.

    Args:
        file_path: Path to the C/C++ source file (.c, .h, .cpp, etc.)
        target: Target product/macro name (e.g., "PRODUCT_A", "DEBUG").
                Branches #ifdef this target are kept; others are pruned.
        compile_db: Path to compile_commands.json for the project.
        mode: Pruning mode - "physical" (remove inactive lines completely)
              or "virtual" (preserve line numbers with [INACTIVE] markers).
              Default is "physical" for maximum token reduction.

    Returns:
        Pruned source code with a summary header showing what was removed.
    """
    pruner_mode = (
        PrunerMode.VIRTUAL_FOLDING
        if mode == "virtual"
        else PrunerMode.PHYSICAL_DELETION
    )

    try:
        pruned = _prune_file(file_path, target, compile_db=compile_db, mode=pruner_mode)
        original_file = (
            resolved if (resolved := _resolve_file_path(file_path)) else file_path
        )

        with open(original_file, "r") as f:
            original_source = f.read()

        original_lines = len(original_source.splitlines())
        pruned_lines = len([l for l in pruned.splitlines() if l.strip()])
        removed = original_lines - pruned_lines
        pct = round(removed / original_lines * 100, 1) if original_lines > 0 else 0.0

        summary = (
            f"/* ── MacroPruner-Ctx ────────────────────────── */\n"
            f"/* Target: {target}                             */\n"
            f"/* Pruned: {removed}/{original_lines} lines ({pct}%) */\n"
            f"/* Mode: {mode}                                     */\n"
            f"/* ───────────────────────────────────────────── */\n\n"
        )

        return summary + pruned

    except FileNotFoundError as e:
        return f"/* Error: {e} */"
    except ValueError as e:
        return f"/* Error: Unclosed conditional directives in source: {e} */"
    except Exception as e:
        return f"/* Error: {type(e).__name__}: {e} */"


# ── Resource: file:// for C/C++ source files ─────────────────

# Register a resource for each file requested via the tool.
# For fully dynamic file paths, users should use the read_c tool
# which supports any file path as an argument.


def main():
    """Start the MCP server via stdio transport."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
