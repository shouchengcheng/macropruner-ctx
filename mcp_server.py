"""
MacroPruner-Ctx MCP Server

Exposes:
  - Tool: read_c(file_path, target) → pruned source code with inactive conditional blocks removed
  - Resource: cfile:///path/to/file → read file (optionally pruned if target parameter provided)
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, Optional

from mcp.server.fastmcp import FastMCP

from pruner_core import PrunerCore, PrunerMode
from cc_parser import CompileDBParser
from skeletonizer import Skeletonizer
from dep_graph import DependencyGraph
from backends import get_backend, list_backends as _list_backends, PruneResult
from config import load as load_config, resolve_compile_db


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
    mode: str = "physical",
    backend: str = "regex",
) -> PruneResult:
    """Prune a C/C++ file via the chosen backend.

    `mode` is 'physical' or 'virtual' (string). `backend` may be
    'regex', 'clang', or 'auto'. The result is a PruneResult that
    includes code, skipped_ranges, and metadata. Falls back to regex
    if the requested backend is unavailable.

    `target` and `compile_db` fall back to .macroprunerrc defaults
    when empty. The config lookup uses CWD as project_root (which is
    almost always the case for MCP — agents launch from the project
    root, not arbitrary subdirs).
    """
    if not target or not compile_db:
        cfg = load_config()
        if not target:
            target = cfg.get("pruner.default_target", "") or "DEFAULT"
        if not compile_db:
            # Resolve relative to cwd (MCP servers run with cwd at
            # project root, which is the user's expectation).
            resolved = resolve_compile_db(cfg, project_root=os.getcwd())
            if resolved:
                compile_db = resolved

    resolved_path = _resolve_file_path(file_path)
    if not resolved_path:
        raise FileNotFoundError(f"Cannot resolve file path: {file_path}")
    if not os.path.isfile(compile_db):
        raise FileNotFoundError(f"compile_commands.json not found: {compile_db}")

    try:
        inst = get_backend(backend)
    except (ValueError, RuntimeError):
        inst = get_backend("regex")

    result = inst.prune(resolved_path, target, compile_db, mode=mode)
    result.effective_target = target
    result.effective_compile_db = compile_db
    return result


# ── Tool: read_c ──────────────────────────────────────────────


@server.tool(
    name="read_c",
    description="Read a C/C++ source file with inactive conditional compilation blocks "
    "(#ifdef, #ifndef, #else, #elif, #endif) pruned away. "
    "Specify the target product/macro to keep active branches for. "
    "Returns only the active code paths with a pruning summary.\n\n"
    "Supports #if expressions beyond simple defined() — see `backend`.\n\n"
    "**Use when:** Analyzing or modifying a single C/C++ file; need full implementation details without irrelevant #ifdef branches.\n"
    "**Do NOT use when:** You only need function signatures (use read_c_skeleton), or you need cross-file context (use read_c_with_deps).\n\n"
    "**Parameters:**\n"
    "- file_path (required): Absolute or relative path to .c/.h/.cpp file\n"
    "- target (required): Product/macro name matching #ifdef in source (e.g., 'PRODUCT_A')\n"
    "  Falls back to `pruner.default_target` from .macroprunerrc if omitted.\n"
    "- compile_db (required): Absolute path to project's compile_commands.json\n"
    "  Falls back to `pruner.compile_db` from .macroprunerrc, or auto-discovered\n"
    "  in build/compile_commands.json.\n"
    "- mode (optional): 'physical' (default, removes inactive lines) or 'virtual' (keeps line numbers with markers)\n"
    "- backend (optional): 'regex' (default, fast pure-Python) | 'clang' (slower, ground truth via clang -E) | 'auto' (prefers clang if available, falls back to regex)",
)
def read_c(
    file_path: str,
    target: str = "",
    compile_db: str = "",
    mode: str = "physical",
    backend: str = "regex",
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
        backend: 'regex' (default, fast), 'clang' (slower ground-truth),
                 or 'auto' (use clang if available, else regex). The
                 clang backend returns fully preprocessed code — useful
                 for cross-validation but not the usual LLM reading.

    Returns:
        Pruned source code with a summary header showing what was removed.
    """
    try:
        result = _prune_file(
            file_path, target, compile_db=compile_db, mode=mode, backend=backend,
        )

        # Build the summary header. The clang backend already prepends
        # its own banner (it knows its output is fully preprocessed).
        if result.backend_name == "clang":
            return result.code

        tok = result.token_estimate
        summary = (
            f"/* --- MacroPruner-Ctx ---------------------------- */\n"
            f"/* Target:    {result.effective_target:<36}*/\n"
            f"/* Lines:     {result.original_lines - result.pruned_lines}/{result.original_lines} dropped "
            f"({result.reduction_percentage}%)              */\n"
            f"/* Tokens:    {tok['saved_tokens']}/{tok['original_tokens']} saved "
            f"({tok['saved_pct']}%)                  */\n"
            f"/* Mode:      {mode}                                */\n"
            f"/* Backend:   {result.backend_name}                              */\n"
            f"/* ------------------------------------------------ */\n\n"
        )
        return summary + result.code

    except FileNotFoundError as e:
        return f"/* Error: {e} */"
    except ValueError as e:
        return f"/* Error: Unclosed conditional directives in source: {e} */"
    except Exception as e:
        return f"/* Error: {type(e).__name__}: {e} */"


# ── Tool: read_c_skeleton ──────────────────────────────────────────────


@server.tool(
    name="read_c_skeleton",
    description="Read a C/C++ source file, prune inactive conditional blocks, "
    "then strip function bodies to produce a structural skeleton. "
    "Keeps struct/enum/typedef definitions, #define/#include directives, "
    "and function signatures. Useful for understanding code structure without implementation details.\n\n"
    "**Use when:** Quick module interface overview; API documentation generation; cross-module dependency analysis; token budget is tight (saves 70-90% vs full code).\n"
    "**Do NOT use when:** You need function implementation details or macro expansion logic.\n\n"
    "**Parameters:**\n"
    "- file_path (required): Absolute or relative path to .c/.h/.cpp file\n"
    "- target (required): Product/macro name matching #ifdef in source\n"
    "  Falls back to `pruner.default_target` from .macroprunerrc if omitted.\n"
    "- compile_db (required): Absolute path to project's compile_commands.json\n"
    "  Falls back to .macroprunerrc / auto-discovery.\n"
    "- mode (optional): 'physical' (default) or 'virtual'",
)
def read_c_skeleton(
    file_path: str,
    target: str = "",
    compile_db: str = "",
    mode: str = "physical",
) -> str:
    """Read, prune, and skeletonize a C/C++ source file.

    Args:
        file_path: Path to the C/C++ source file (.c, .h, .cpp, etc.)
        target: Target product/macro name (e.g., "PRODUCT_A", "DEBUG").
        compile_db: Path to compile_commands.json for the project.
        mode: Pruning mode - "physical" or "virtual". Default is "physical".

    Returns:
        Skeletonized source code with function bodies replaced by { /* ... */ }
    """
    try:
        result = _prune_file(file_path, target, compile_db=compile_db, mode=mode)
        pruned = result.code

        skel = Skeletonizer()
        skeleton = skel.skeletonize(pruned)
        stats = skel.get_stats()

        summary = (
            f"/* ── MacroPruner-Ctx (Skeleton) ─────────────── */\n"
            f"/* Target: {target}                             */\n"
            f"/* Original: {result.original_lines} lines              */\n"
            f"/* Skeleton: {stats['skeleton_lines']} lines             */\n"
            f"/* Functions stripped: {stats['functions_stripped']}                  */\n"
            f"/* Backend: {result.backend_name}                          */\n"
            f"/* ───────────────────────────────────────────── */\n\n"
        )

        return summary + skeleton

    except FileNotFoundError as e:
        return f"/* Error: {e} */"
    except ValueError as e:
        return f"/* Error: Unclosed conditional directives in source: {e} */"
    except Exception as e:
        return f"/* Error: {type(e).__name__}: {e} */"


# ── Tool: apply_patch ──────────────────────────────────────────────


@server.tool(
    name="apply_patch",
    description="Apply a unified diff patch to the original C/C++ source file. "
    "The LLM should generate a diff after reading pruned code via read_c, "
    "then call this tool to write changes back to the original file.\n\n"
    "**Use when:** Writing back modifications suggested by the LLM after analyzing pruned code. Ensures minimal, traceable changes.\n"
    "**Do NOT use when:** Reading or analyzing code (use read_c/read_c_skeleton/read_c_with_deps instead).\n\n"
    "**Parameters:**\n"
    "- file_path (required): Absolute or relative path to .c/.h/.cpp file to patch\n"
    "- diff (required): Unified diff string (format: '--- a/path\\n+++ b/path\\n@@ ...')",
)
def apply_patch(file_path: str, diff: str) -> str:
    """Apply a unified diff patch to a C/C++ source file.

    Args:
        file_path: Path to the C/C++ source file (.c, .h, .cpp, etc.)
        diff: Unified diff format string (output of git diff or similar)

    Returns:
        Success message or error details.
    """
    resolved = _resolve_file_path(file_path)
    if not resolved:
        raise FileNotFoundError(f"Cannot resolve file path: {file_path}")

    try:
        result = subprocess.run(
            ["git", "apply", "--check"],
            input=diff,
            text=True,
            capture_output=True,
            cwd=os.path.dirname(resolved),
        )
        if result.returncode != 0:
            return f"/* Patch validation failed:\n{result.stderr} */"

        result = subprocess.run(
            ["git", "apply"],
            input=diff,
            text=True,
            capture_output=True,
            cwd=os.path.dirname(resolved),
        )
        if result.returncode != 0:
            return f"/* Patch application failed:\n{result.stderr} */"

        return f"/* Patch applied successfully to {os.path.basename(resolved)} */"

    except Exception as e:
        return f"/* Error: {type(e).__name__}: {e} */"


# ── Tool: read_c_with_deps ──────────────────────────────────────────────


@server.tool(
    name="read_c_with_deps",
    description="Read a C/C++ source file with its #include dependencies. "
    "The target file is returned as fully pruned source code. "
    "Dependency files are returned as pruned skeletons (signatures only). "
    "This provides multi-file context in a single call, optimized for LLM token efficiency.\n\n"
    "**Use when:** Analyzing cross-file function calls; understanding struct/enum definitions across headers; debugging include-related issues; need multi-file context but token budget is limited (saves ~80% vs naive full paste).\n"
    "**Do NOT use when:** Single-file analysis is sufficient (use read_c) or you only need signatures (use read_c_skeleton).\n\n"
    "**Parameters:**\n"
    "- file_path (required): Absolute or relative path to primary .c/.h/.cpp file\n"
    "- target (required): Product/macro name matching #ifdef in source\n"
    "  Falls back to .macroprunerrc.\n"
    "- compile_db (required): Absolute path to project's compile_commands.json\n"
    "  Falls back to .macroprunerrc / auto-discovery.\n"
    "- mode (optional): 'physical' (default) or 'virtual'\n"
    "- max_depth (optional): Maximum include depth to traverse (default: 2, range: 1-5)",
)
def read_c_with_deps(
    file_path: str,
    target: str = "",
    compile_db: str = "",
    mode: str = "physical",
    max_depth: int = 2,
) -> str:
    """Read a C/C++ file with pruned dependency context.

    Args:
        file_path: Path to the primary C/C++ source file.
        target: Target product/macro name for conditional compilation.
        compile_db: Path to compile_commands.json.
        mode: Pruning mode - "physical" or "virtual". Default "physical".
        max_depth: Maximum include depth to traverse. Default 2.

    Returns:
        Structured output with target file (full pruned) and dependencies (skeletons).
    """
    try:
        # Apply config fallback first so compile_db is populated
        # before we try to read include dirs.
        if not target or not compile_db:
            cfg = load_config()
            if not target:
                target = cfg.get("pruner.default_target", "") or "DEFAULT"
            if not compile_db:
                resolved_cdb = resolve_compile_db(cfg, project_root=os.getcwd())
                if resolved_cdb:
                    compile_db = resolved_cdb

        resolved = _resolve_file_path(file_path)
        if not resolved:
            return f"/* Error: Cannot resolve file path: {file_path} */"

        if not os.path.isfile(compile_db):
            return f"/* Error: compile_commands.json not found: {compile_db} */"

        parser = CompileDBParser(compile_db)
        include_dirs = parser.resolve_include_dirs(resolved)

        base_dir = os.path.dirname(resolved)
        abs_include_dirs = [
            d if os.path.isabs(d) else os.path.join(base_dir, d) for d in include_dirs
        ]

        # Stage 3 Phase 2: build the dependency graph with macro
        # awareness. An #include nested inside an inactive #if block
        # is NOT followed, which avoids the LLM seeing a struct defined
        # in a header that the target product never pulls in.
        db_macros = parser.extract_macros(resolved)
        active_macros: Dict[str, Optional[str]] = {
            target.upper(): None,
            f"TARGET_{target.upper()}": None,
            f"PRODUCT_{target.upper()}": None,
        }
        for k, v in db_macros.items():
            active_macros[k] = v

        dg = DependencyGraph()
        dg.conditional_build(
            resolved, include_dirs=abs_include_dirs,
            max_depth=max_depth, active_macros=active_macros,
        )

        root_basename = os.path.basename(resolved)
        # We follow only paths the conditional traversal actually
        # entered. `dg.resolved_paths` still contains the root, so
        # the filter below excludes just the root.
        dep_basenames = [
            b for b in dg.resolved_paths
            if b != root_basename and " [skipped]" not in b
        ]

        sections = []

        target_result = _prune_file(
            resolved, target, compile_db=compile_db, mode=mode
        )
        target_pruned = target_result.code

        sections.append(
            f"/* ══ TARGET FILE: {root_basename} ══════════════════ */\n"
            f"/* Original: {target_result.original_lines} lines | Pruned: {target_result.pruned_lines} lines */\n\n"
            f"{target_pruned}"
        )

        for dep_basename in sorted(dep_basenames):
            dep_path = dg.resolved_paths[dep_basename]
            if not os.path.isfile(dep_path):
                continue
            try:
                dep_result = _prune_file(
                    dep_path, target, compile_db=compile_db, mode=mode
                )
                skel = Skeletonizer()
                dep_skeleton = skel.skeletonize(dep_result.code)
                stats = skel.get_stats()

                sections.append(
                    f"\n/* ══ DEPENDENCY: {dep_basename} ══════════════════ */\n"
                    f"/* Skeleton: {stats['skeleton_lines']} lines | "
                    f"Functions stripped: {stats['functions_stripped']} */\n\n"
                    f"{dep_skeleton}"
                )
            except Exception:
                sections.append(
                    f"\n/* ══ DEPENDENCY: {dep_basename} ══════════════════ */\n"
                    f"/* Skipped: could not process */\n"
                )

        header = (
            f"/* ── MacroPruner-Ctx (with deps) ──────────────── */\n"
            f"/* Target: {target}                                */\n"
            f"/* Root: {root_basename}                           */\n"
            f"/* Dependencies: {len(dep_basenames)} files        */\n"
            f"/* Max depth: {max_depth}                          */\n"
            f"/* Mode: {mode}                                    */\n"
            f"/* Backend: {target_result.backend_name}                          */\n"
            f"/* ─────────────────────────────────────────────── */\n\n"
        )

        return header + "\n".join(sections)

    except ValueError as e:
        return f"/* Error: Unclosed conditional directives: {e} */"
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
