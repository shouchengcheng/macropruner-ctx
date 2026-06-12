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
import time
from pathlib import Path
from typing import Dict, Optional

from mcp.server.fastmcp import FastMCP

from pruner_core import PrunerCore, PrunerMode
from cc_parser import CompileDBParser
from skeletonizer import Skeletonizer
from dep_graph import DependencyGraph
from backends import get_backend, list_backends as _list_backends, PruneResult
from config import load as load_config, resolve_compile_db
from errors import FatalError, TransientError, format_error, with_fallback
from patch_applier import (
    apply_unified_diff as _apply_diff,
    check_c_syntax as _check_c_syntax,
    PatchError,
)
from bootstrap import scan as _bootstrap_scan, apply as _bootstrap_apply


def _has_macroprunerrc() -> bool:
    """Quick check: does the config system find a .macroprunerrc?"""
    cfg = load_config()
    return bool(cfg.get("_config_path"))


def _is_readonly_mode() -> bool:
    """True if the process is running in read-only mode.

    In read-only mode, apply_patch is refused at the MCP tool boundary
    (so even an LLM that didn't see the env var can't write). The
    CLI also honours this flag. Set via:

        export MACROPRUNER_READONLY=1

    Useful for: read-only CI smoke tests, audit-only sessions, code
    review workflows where the LLM should see the pruner but never
    write anything.
    """
    return os.environ.get("MACROPRUNER_READONLY", "").lower() in ("1", "true", "yes", "on")


# Default cap for read_c output (P15-1). 50 KB is large enough
# for most real C files, small enough that the LLM doesn't choke.
# Override with `max_bytes=` per call.
DEFAULT_MAX_BYTES = 50_000


def _apply_truncation(
    text: str,
    offset: int = 0,
    max_bytes: int = 0,
) -> str:
    """Truncate the output to <= max_bytes characters, optionally
    starting at `offset` bytes into `text`.

    This is the last line of defense against the LLM receiving
    an output so large that the LLM provider truncates the
    response (the "Response truncated due to output length limit"
    error). The cap applies to character count (which approximates
    bytes for ASCII C code).

    If truncation occurs, a [WARN] banner is appended that tells
    the LLM how to ask for more (offset=N).

    Args:
        text: The full output (banner + pruned code).
        offset: Byte offset to start at. 0 = start of text.
        max_bytes: Hard cap. 0 means use DEFAULT_MAX_BYTES.

    Returns:
        Possibly-truncated text. If truncated, the returned text
        ends with a [WARN] banner. If `offset >= len(text)`, the
        banner reports the overflow without further truncation.
    """
    if max_bytes <= 0:
        max_bytes = DEFAULT_MAX_BYTES

    if offset < 0:
        offset = 0
    if offset >= len(text):
        # Caller asked for an offset past the end. Return a banner
        # only; the LLM can adjust.
        return (
            f"/* [WARN] Truncated: offset {offset} >= total size "
            f"{len(text)} bytes. The file may have been pruned "
            f"smaller than expected. Try offset=0 to start over. */"
        )

    # If text is small enough, return as-is.
    if len(text) - offset <= max_bytes:
        return text

    # Truncate to the cap. Try to break on a line boundary for
    # readability (so the LLM doesn't see a half-line).
    end = offset + max_bytes
    if end < len(text):
        # Find the last newline <= end.
        nl = text.rfind("\n", offset, end)
        if nl > offset + max_bytes // 2:
            end = nl  # break on the newline, not deep into the cap

    truncated = text[:end]
    remaining = len(text) - end
    return (
        truncated
        + f"\n/* [WARN] Truncated: showing bytes {offset}-{end} of "
        f"{len(text)} total. {remaining} bytes remaining. */\n"
        + f"/* To continue, call read_c(file_path=..., offset={end}). */\n"
    )


def _check_path_safe(file_path: str, allowlist, denylist) -> Optional[FatalError]:
    """Validate file_path against the project's allowlist/denylist.

    Returns None if the path is safe; a FatalError with a descriptive
    hint otherwise. Used by read_c / read_c_skeleton / read_c_with_deps
    / apply_patch to refuse paths outside the project root.

    An empty allowlist means "no restriction" (the legacy
    behaviour, kept for backwards compatibility). An empty
    denylist means "deny nothing".
    """
    if not allowlist and not denylist:
        return None  # no restrictions configured

    # Resolve to an absolute path; this also collapses `..` etc.
    try:
        resolved = os.path.realpath(file_path)
    except (TypeError, ValueError):
        return FatalError(
            f"Invalid file_path: {file_path!r}",
            hint="file_path must be a string",
        )

    # Denylist: check if the resolved path is inside any denylisted
    # directory. We use os.path.commonpath for the "is-inside"
    # check (handles trailing slashes correctly).
    for denied in denylist:
        denied_abs = os.path.realpath(denied)
        try:
            common = os.path.commonpath([resolved, denied_abs])
        except ValueError:
            continue  # different drives on Windows
        if common == denied_abs:
            return FatalError(
                f"file_path is under a denylisted root: {file_path!r} "
                f"(matches {denied!r})",
                hint="remove the path from pruner.path_denylist in .macroprunerrc "
                     "if the user actually wants to touch it",
            )

    # Allowlist: if non-empty, the path must be under at least one
    # allowed root. If allowlist is empty, all paths are allowed
    # (subject to the denylist check above).
    if not allowlist:
        return None

    for allowed in allowlist:
        allowed_abs = os.path.realpath(allowed)
        try:
            common = os.path.commonpath([resolved, allowed_abs])
        except ValueError:
            continue
        if common == allowed_abs:
            return None

    return FatalError(
        f"file_path is not under any allowlisted root: {file_path!r}",
        hint="add the path's parent directory to pruner.path_allowlist in "
             ".macroprunerrc, or remove the allowlist to allow all paths",
    )


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
    token_budget: int = 0,
    sysroot: str = "",
    extra_target: str = "",
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

    `token_budget` (Stage 4) caps the output size. If the pruned
    code exceeds the budget, the call automatically degrades to a
    skeletonized view. 0 disables the cap.

    `sysroot` and `extra_target` are clang-backend specific. They
    override the auto-detection from the compile_db entry's command
    — useful for cross-compile SDKs where the default sysroot
    clang picks is wrong.
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
        # Pull token_budget from config if not already set by the caller.
        # The caller can override per-call; this is just a default.
        budget_from_cfg = int(cfg.get("pruner.token_budget", 0))
        if token_budget == 0 and budget_from_cfg:
            token_budget = budget_from_cfg
        # Same idea for sysroot / extra_target: read from config when
        # the caller didn't pass them. Useful for cross-compile SDKs
        # where the LLM probably doesn't know the sysroot path.
        if not sysroot:
            sysroot = cfg.get("pruner.sysroot", "") or ""
        if not extra_target:
            extra_target = cfg.get("pruner.extra_target", "") or ""
        # Path safety check (P12-1). Refuse the call if file_path
        # is outside the allowlist or inside the denylist.
        allowlist = cfg.get("pruner.path_allowlist", []) or []
        denylist = cfg.get("pruner.path_denylist", []) or []
        path_err = _check_path_safe(file_path, allowlist, denylist)
        if path_err is not None:
            raise path_err

    resolved_path = _resolve_file_path(file_path)
    if not resolved_path:
        raise FileNotFoundError(f"Cannot resolve file path: {file_path}")
    if not os.path.isfile(compile_db):
        raise FileNotFoundError(f"compile_commands.json not found: {compile_db}")

    try:
        inst = get_backend(backend, sysroot=sysroot or None, extra_target=extra_target or None)
    except (ValueError, RuntimeError):
        inst = get_backend("regex")

    result = inst.prune(resolved_path, target, compile_db, mode=mode)
    result.effective_target = target
    result.effective_compile_db = compile_db

    # Stage 4: enforce token budget. If the pruned output is over
    # the caller's budget, fall back to the skeleton view (which
    # strips function bodies and is typically 70-90% smaller than
    # the fully-pruned code). If even the skeleton is over budget,
    # tag the result as a WARN so the LLM can decide to chunk
    # further or ignore the budget for this call.
    if token_budget and token_budget > 0:
        result = _enforce_budget(
            result, token_budget, resolved_path, target, compile_db, mode, backend,
        )

    return result


def _enforce_budget(
    result: PruneResult,
    budget: int,
    file_path: str,
    target: str,
    compile_db: str,
    mode: str,
    backend: str,
) -> PruneResult:
    """Auto-degrade a PruneResult that exceeds the token budget.

    Strategy:
      1. If pruned_tokens <= budget: return as-is.
      2. Otherwise, run Skeletonizer over the pruned code. If the
         skeleton fits: return skeleton with extra metadata.
      3. If neither fits: keep the pruned code but tag the result
         with a WARN — the LLM will see it via PruneResult.extra.
    """
    tok = result.token_estimate
    if tok["pruned_tokens"] <= budget:
        return result

    # Try skeletonize.
    skel = Skeletonizer()
    skeleton = skel.skeletonize(result.code)
    skel_stats = skel.get_stats()
    # Re-estimate tokens on the skeleton (char count is what
    # matters for the budget check; the skeleton body is plain C).
    from token_counter import char_estimate
    skel_tokens = char_estimate(skeleton)

    if skel_tokens <= budget:
        # Skeleton fits. Replace the code in the result.
        result.code = skeleton
        result.pruned_lines = skel_stats["skeleton_lines"]
        result.extra["budget_degraded"] = "skeleton"
        result.extra["budget_pruned_tokens"] = str(tok["pruned_tokens"])
        result.extra["budget_skel_tokens"] = str(skel_tokens)
        return result

    # Neither fits. Tag as over-budget; let the caller decide.
    result.extra["budget_exceeded"] = "true"
    result.extra["budget_requested"] = str(budget)
    result.extra["budget_pruned_tokens"] = str(tok["pruned_tokens"])
    result.extra["budget_skel_tokens"] = str(skel_tokens)
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
    "- backend (optional): 'regex' (default, fast pure-Python) | 'clang' (slower, ground truth via clang -E) | 'auto' (prefers clang if available, falls back to regex)\n"
    "- token_budget (optional): Maximum LLM tokens for the output. 0 = no cap (default). If exceeded, output auto-degrades to skeleton (function bodies stripped). Result tagged with [WARN] if even the skeleton exceeds the cap.\n"
    "- sysroot (optional): Clang-backend only. Path to a cross-compile SDK's sysroot (e.g. /opt/ws63/tools/sysroot). If omitted, auto-detected from the compile_db entry's --sysroot= flag. Falls back to clang's default sysroot for native builds.\n"
    "- extra_target (optional): Clang-backend only. --target= value to pass to clang (e.g. 'riscv32-linux-musl'). Auto-detected from the compile_db entry if not given.\n"
    "- offset (optional): Byte offset for paginating large outputs. 0 = start. Use this with the [WARN] Truncated banner to read more of the file.\n"
    "- max_bytes (optional): Maximum bytes to return. 0 = use the system default (~50KB). When the output exceeds this cap, the call returns the first max_bytes and adds a [WARN] Truncated banner. Set higher if you know the file is large; lower to fit a tight token budget.\n",
)
def read_c(
    file_path: str,
    target: str = "",
    compile_db: str = "",
    mode: str = "physical",
    backend: str = "regex",
    token_budget: int = 0,
    sysroot: str = "",
    extra_target: str = "",
    offset: int = 0,
    max_bytes: int = 0,
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
        t0 = time.monotonic_ns()
        result = _prune_file(
            file_path, target, compile_db=compile_db, mode=mode, backend=backend,
            token_budget=token_budget, sysroot=sysroot, extra_target=extra_target,
        )
        # Stage 4 / skeletonization happen inside _prune_file; we measure
        # the whole call here so the user sees one consistent number.
        elapsed_ms = (time.monotonic_ns() - t0) / 1e6

        # Build the summary header. The clang backend already prepends
        # its own banner (it knows its output is fully preprocessed).
        if result.backend_name == "clang":
            # Inject the latency into the clang banner so the user still
            # sees a single timing signal even though we don't add our own
            # banner for the clang oracle.
            return result.code.replace(
                "/* --- MacroPruner-Ctx (Clang Backend / Oracle) --- */",
                f"/* --- MacroPruner-Ctx (Clang Backend / Oracle) ---\n"
                f"/* Time:      {elapsed_ms:>6.1f} ms                              */",
                1,
            )

        tok = result.token_estimate

        # Detect budget-related degradation.
        degraded_to = result.extra.get("budget_degraded", "")
        over_budget = result.extra.get("budget_exceeded", "false") == "true"
        budget_line = ""
        if degraded_to:
            budget_line = (
                f"/* Degraded: {degraded_to:<32} */\n"
            )
        elif over_budget:
            budget_line = (
                f"/* [WARN] Over budget: pruned={result.extra.get('budget_pruned_tokens', '?')}, "
                f"skel={result.extra.get('budget_skel_tokens', '?')}, "
                f"cap={result.extra.get('budget_requested', '?')} */\n"
            )

        summary = (
            f"/* --- MacroPruner-Ctx ---------------------------- */\n"
            f"/* Target:    {result.effective_target:<36}*/\n"
            f"/* Lines:     {result.original_lines - result.pruned_lines}/{result.original_lines} dropped "
            f"({result.reduction_percentage}%)              */\n"
            f"/* Tokens:    {tok['saved_tokens']}/{tok['original_tokens']} saved "
            f"({tok['saved_pct']}%)                  */\n"
            f"/* Mode:      {mode}                                */\n"
            f"/* Backend:   {result.backend_name}                              */\n"
            f"/* Time:      {elapsed_ms:>6.1f} ms                              */\n"
            + (budget_line if budget_line else "")
            + f"/* ------------------------------------------------ */\n\n"
        )
        return _apply_truncation(
            summary + result.code, offset=offset, max_bytes=max_bytes,
        )

    except FileNotFoundError as e:
        return FatalError(
            str(e),
            hint="verify the path exists, or call bootstrap_config() to auto-generate "
                 "the config, or drop a .macroprunerrc with 'compile_db = ...'",
        ).formatted()
    except ValueError as e:
        return FatalError(str(e), hint="check argument types").formatted()
    except Exception as e:
        return format_error(e)


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
            f"/* --- MacroPruner-Ctx (Skeleton) ----------------- */\n"
            f"/* Target:    {result.effective_target:<36}*/\n"
            f"/* Original:  {result.original_lines} lines                             */\n"
            f"/* Skeleton:  {stats['skeleton_lines']} lines                            */\n"
            f"/* Stripped:  {stats['functions_stripped']} functions                       */\n"
            f"/* Backend:   {result.backend_name}                              */\n"
            f"/* ------------------------------------------------ */\n\n"
        )

        return summary + skeleton

    except FileNotFoundError as e:
        return FatalError(str(e), hint="verify the path exists").formatted()
    except ValueError as e:
        return FatalError(str(e), hint="check argument types").formatted()
    except Exception as e:
        return format_error(e)


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
    "- diff (required): Unified diff string (format: '--- a/path\\n+++ b/path\\n@@ ...')\n\n"
    "**Backend:** Tries `git apply --check` first when the file is in a git repo (fast path). "
    "Falls back to a built-in pure-Python applier for non-git workflows. After applying, "
    "runs a lightweight C/C++ syntax sanity check (brace balance, #if/#endif balance) and "
    "tags any warnings in the response.",
)
def apply_patch(file_path: str, diff: str) -> str:
    """Apply a unified diff patch to a C/C++ source file.

    Args:
        file_path: Path to the C/C++ source file (.c, .h, .cpp, etc.)
        diff: Unified diff format string (output of git diff or similar)

    Returns:
        Success message or [FATAL]/[WARN]-tagged error details.
    """
    # Read-only mode: refuse all writes. This is a hard block at the
    # MCP tool boundary — the LLM cannot bypass it by failing to
    # notice the env var. Use for audit-only sessions, CI smoke
    # tests, or any context where you want the LLM to see the pruner
    # but not modify anything.
    if _is_readonly_mode():
        return FatalError(
            "apply_patch refused: MACROPRUNER_READONLY=1 is set",
            hint="unset the env var to allow writes, or use read_c instead",
        ).formatted()

    # Path safety check (P12-1). Refuse if file_path is outside
    # the project's allowlist or inside a denylisted root.
    cfg = load_config()
    allowlist = cfg.get("pruner.path_allowlist", []) or []
    denylist = cfg.get("pruner.path_denylist", []) or []
    path_err = _check_path_safe(file_path, allowlist, denylist)
    if path_err is not None:
        return path_err.formatted()

    resolved = _resolve_file_path(file_path)
    if not resolved:
        return FatalError(
            f"Cannot resolve file path: {file_path}",
            hint="check the path exists and is readable",
        ).formatted()

    # Read the original content. This is what we'll patch.
    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            original = f.read()
    except (OSError, UnicodeDecodeError) as e:
        return FatalError(str(e), hint="check file permissions and encoding").formatted()

    # Try the git fast path first. If the file is in a git repo and
    # `git apply` succeeds, that's the most reliable thing. Otherwise
    # (or if git isn't installed), fall back to our built-in applier.
    cwd = os.path.dirname(resolved)
    new_content: Optional[str] = None
    used_path: str = ""

    if _is_in_git_repo(cwd):
        check = subprocess.run(
            ["git", "apply", "--check"],
            input=diff, text=True, capture_output=True, cwd=cwd,
        )
        if check.returncode == 0:
            apply = subprocess.run(
                ["git", "apply"],
                input=diff, text=True, capture_output=True, cwd=cwd,
            )
            if apply.returncode == 0:
                used_path = "git"
                new_content = None  # git wrote the file
            else:
                return FatalError(
                    f"git apply failed: {apply.stderr.strip()}",
                    hint="the diff does not match the current file state",
                ).formatted()
        else:
            # git apply --check rejected the diff. Either the file
            # has drifted from what the diff was generated against,
            # or the diff is malformed. Try our built-in applier
            # as a sanity check, but only if the diff is at least
            # structurally well-formed.
            pass  # fall through to built-in

    if new_content is None and used_path == "":
        # Built-in applier.
        try:
            new_content = _apply_diff(original, diff)
            used_path = "builtin"
        except PatchError as e:
            return FatalError(
                str(e),
                hint="re-generate the diff against the current file content",
            ).formatted()

    # Write the new content (only if we used the built-in path; git
    # already wrote the file).
    if used_path == "builtin":
        try:
            with open(resolved, "w", encoding="utf-8") as f:
                f.write(new_content)  # type: ignore[arg-type]
        except (OSError, PermissionError) as e:
            return FatalError(
                str(e), hint="the file is not writable; check permissions"
            ).formatted()

    # Post-apply syntax sanity check. Warnings are returned alongside
    # the success message; they don't fail the call.
    final_content = new_content if new_content is not None else _read_file(resolved)
    warnings = _check_c_syntax(final_content)
    if warnings:
        warn_block = "\n".join(f"  - {w}" for w in warnings)
        return (
            f"[OK] Patch applied to {os.path.basename(resolved)} via {used_path}.\n"
            f"[WARN] Syntax check found {len(warnings)} issue(s):\n{warn_block}"
        )
    return f"[OK] Patch applied to {os.path.basename(resolved)} via {used_path}."


def _is_in_git_repo(directory: str) -> bool:
    """True if `directory` is inside a git working tree.

    Cheap check: look for a .git entry anywhere up the tree.
    """
    cur = os.path.abspath(directory)
    while True:
        if os.path.isdir(os.path.join(cur, ".git")) or os.path.isfile(
            os.path.join(cur, ".git")
        ):
            return True
        parent = os.path.dirname(cur)
        if parent == cur:
            return False
        cur = parent


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


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


# ── Tool: bootstrap_config — P14: auto-generate .macroprunerrc ─


@server.tool(
    name="bootstrap_config",
    description="Auto-generate .macroprunerrc for the current project. "
    "Scans PROJECT_MANIFEST.md (init-project skill artifact) or "
    "compile_commands.json to infer the default target, compile_db, "
    "and path allowlist. Dry-run by default; call with apply=True to "
    "write the file.\n\n"
    "**When to use:** First-time setup of macropruner in a project. "
    "The tool only appears in the tool list when no .macroprunerrc is "
    "detected (so you won't accidentally overwrite an existing config).\n\n"
    "**How to use:**\n"
    "  1. Call bootstrap_config() (dry-run) — read the recommendation\n"
    "  2. Call bootstrap_config(apply=True) — write the file\n"
    "  3. Review the generated .macroprunerrc\n"
    "  4. Use read_c as normal — it will pick up the config automatically",
)
def bootstrap_config(
    apply: bool = False,
    force: bool = False,
    project_root: str = "",
) -> str:
    """Auto-generate or preview .macroprunerrc for the current project.

    Args:
        apply: If True, write the generated config to disk. Default
               is dry-run (preview only).
        force: If True, overwrite an existing .macroprunerrc (if any).
               Default is to refuse if a config already exists.
        project_root: Absolute path to the project root. Default: CWD.

    Returns:
        Dry-run: The recommended .macroprunerrc content as a code block.
        Apply:   A confirmation message with the path written.

    The tool's presence in the tool list is conditional: it only
    appears when no .macroprunerrc is found by the config system.
    """
    try:
        root = project_root or os.getcwd()
        result = _bootstrap_scan(project_root=root)

        if result["target"] == "DEFAULT" and result["compile_db"] is None:
            return (
                "# [WARN] bootstrap_config could not find a "
                "compile_commands.json.\n"
                "# Consider running the build first to generate one, then "
                "re-run bootstrap_config.\n"
                f"#\n"
                f"# rc_path: {result['rc_path']}\n"
                f"# source:  {result['source']}\n"
            )

        if not apply:
            # Dry-run: render the recommendation.
            lines = [
                f"# macropruner-ctx bootstrap (dry-run)",
                f"# source:  {result['source']}",
                f"# target:  {result['target']}",
                f"# cdb:     {result['compile_db'] or '(not found)'}",
                f"# rc_path: {result['rc_path']}",
                f"",
            ]
            if result["rc_already_exists"]:
                lines.append(
                    "# NOTE: .macroprunerrc already exists at rc_path. "
                    "Call with apply=True, force=True to overwrite."
                )
            lines.append("")
            lines.append("Recommended .macroprunerrc:")
            lines.append("=" * 30)
            # Import _format_rc
            from bootstrap import _format_rc
            comments = {
                "pruner.default_target":
                    "Target product/macro (e.g. PRODUCT_3, ws63).",
                "pruner.compile_db":
                    "Path to compile_commands.json (relative to project root).",
                "pruner.default_backend":
                    "Backend: 'regex' (default), 'clang' (oracle), or 'auto'.",
                "pruner.default_mode":
                    "Pruning mode: 'physical' or 'virtual'.",
                "pruner.default_max_depth":
                    "include tree depth (1-5) for read_c_with_deps.",
                "pruner.token_budget":
                    "Per-call token cap. 0 = unlimited.",
                "pruner.path_allowlist":
                    "Paths the pruner may read/write. Empty = no restriction.",
                "pruner.path_denylist":
                    "Paths always blocked (subtree match).",
            }
            rc_text = _format_rc(result["recommended"], comments)
            lines.append(rc_text.rstrip())
            lines.append("")
            lines.append("To write this config, call:")
            lines.append("  bootstrap_config(apply=True)")
            return "\n".join(lines)

        # Apply: write the file.
        write_result = _bootstrap_apply(project_root=root, force=force)
        if not write_result["written"]:
            reason = write_result.get("refused_reason", "unknown")
            return (
                f"[WARN] bootstrap_config refused: {reason}\n\n"
                f"  To overwrite the existing config, call:\n"
                f"    bootstrap_config(apply=True, force=True)"
            )

        return (
            f"[OK] .macroprunerrc written to:\n"
            f"  {write_result['rc_path']}\n"
            f"\n"
            f"Settings:\n"
            f"  target      = {write_result['target']}\n"
            f"  compile_db  = {write_result['recommended'].get('pruner.compile_db', '?')}\n"
            f"  source      = {write_result['source']}\n"
            f"\n"
            f"Recommended next steps:\n"
            f"  1. Review the file at {write_result['rc_path']}\n"
            f"  2. Add it to git: git add {write_result['rc_path']}\n"
            f"  3. Try: read_c(file_path='src/main.c') — it should "
            f"work now without passing target/compile_db.\n"
        )

    except Exception as e:
        return format_error(e)


# ── Resource: file:// for C/C++ source files ─────────────────

# Register a resource for each file requested via the tool.
# For fully dynamic file paths, users should use the read_c tool
# which supports any file path as an argument.


def main():
    """Start the MCP server via stdio transport."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
