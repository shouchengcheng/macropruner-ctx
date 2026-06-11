"""ClangBackend — invokes the real C preprocessor for ground-truth
pruning. Useful as a cross-validation oracle for the regex backend,
and the only backend that handles truly pathological #if expressions
(includes nested function-like macros, comma operators, sizeof, etc.).

Tradeoff: the output is FULLY preprocessed (macros expanded, includes
inlined, comments stripped). This is not what an LLM wants for reading
the original structure, so by default we run the regex backend and only
fall back to clang in two situations:

  1. The user explicitly asks for backend='clang' (wants ground truth).
  2. The user sets backend='auto' AND the regex backend raises a parse
     error on some #if expression (i.e. the evaluator gives up).

Either way, the result is tagged so callers can distinguish the two
flavors without re-parsing the code.

Implementation notes
---------------------
We invoke `clang -E -dD -w` on the file with the compile_db's -D and
-I flags. The output preserves `# N "file"` line markers, which we use
to:
  1. Track which original-file lines survived preprocessing (= active).
  2. Strip the markers from the returned code (LLM should not see them).

We deliberately do NOT call `clang -cc1 -ast-dump` or any AST-level
API — that requires the libclang Python binding (not installed in the
target environments this project supports). Plain `clang -E` is enough
for the cross-validation use case and is universally available.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

from cc_parser import CompileDBParser

from .base import PruneResult, PrunerBackend, register_backend


_CLANG_CANDIDATES = ("clang", "clang-14", "clang-13", "clang-12", "clang-11", "clang-10")

# `# N "file"` or `# N "file" N2` markers emitted by clang -E.
_LINE_MARKER_RE = re.compile(r'^\s*#\s+(\d+)\s+"([^"]+)"')


def _find_clang() -> Optional[str]:
    """Locate a usable clang binary on PATH."""
    for name in _CLANG_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def _is_cpp(filename: str) -> bool:
    """Heuristic: .cpp/.cc/.cxx/.C are C++; everything else is C."""
    suffix = os.path.splitext(filename)[1].lower()
    return suffix in (".cpp", ".cc", ".cxx", ".c++")


@register_backend
class ClangBackend(PrunerBackend):
    """Backend that runs the actual clang preprocessor.

    The output is fully preprocessed code. It is not a drop-in for
    regex-backend output (which preserves the original C structure).
    """

    name = "clang"

    def __init__(self, timeout: float = 15.0):
        self._clang_path: Optional[str] = None
        self._timeout = timeout
        self._found = _find_clang()
        if self._found:
            self._clang_path = self._found

    def is_available(self) -> Tuple[bool, str]:
        if self._clang_path is None:
            return False, "no clang binary found on PATH (tried: %s)" % ", ".join(
                _CLANG_CANDIDATES
            )
        return True, ""

    def prune(
        self,
        file_path: str,
        target: str,
        compile_db: str,
        mode: str = "physical",
    ) -> PruneResult:
        if self._clang_path is None:
            raise RuntimeError("ClangBackend.is_available() returned False")

        # Resolve file (MCP accepts relative paths).
        if not os.path.isabs(file_path):
            file_path = os.path.abspath(file_path)
        if not os.path.isfile(file_path):
            raise FileNotFoundError(file_path)

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            original_source = f.read()
        original_lines = original_source.count("\n") + (0 if original_source.endswith("\n") else 1)

        # Build clang command.
        # We deliberately do NOT use -dD: we don't want clang's built-in
        # macro defs (e.g. __llvm__, __GNUC__) polluting the output.
        # Line markers (`# N "file"`) are emitted by -E by default, which
        # is exactly what we need to map preprocessed output back to the
        # original file's line numbers.
        cmd = [self._clang_path, "-E", "-w"]
        if _is_cpp(file_path):
            cmd.append("-x")
            cmd.append("c++")
        else:
            cmd.append("-x")
            cmd.append("c")

        # Pull -D and -I from compile_db if available.
        try:
            parser = CompileDBParser(compile_db)
            macros = parser.extract_macros(file_path)
            include_dirs = parser.resolve_include_dirs(file_path)
        except Exception:
            macros = {}
            include_dirs = []

        # Add the target as an active macro (mirrors regex backend's
        # _get_active_macros_for_target).
        target_upper = target.upper()
        macros.setdefault(target_upper, None)
        macros.setdefault(f"TARGET_{target_upper}", None)
        macros.setdefault(f"PRODUCT_{target_upper}", None)

        for name, value in macros.items():
            if value is None:
                cmd.append(f"-D{name}")
            else:
                cmd.append(f"-D{name}={value}")
        for inc in include_dirs:
            cmd.append(f"-I{inc}")

        cmd.append(file_path)

        # Run.
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"clang -E timed out after {self._timeout}s on {file_path}") from e
        if proc.returncode != 0:
            raise RuntimeError(
                f"clang -E failed (rc={proc.returncode}) on {file_path}: "
                f"{proc.stderr[:500]}"
            )

        preprocessed = proc.stdout
        # Walk the output, tracking the "current original line" via
        # line markers. We mark a contiguous range of original lines as
        # active when the output contains source code (not just a marker).
        # CRUCIAL: when a line marker points at a DIFFERENT file (e.g.
        # stdio.h just got included), we must reset current_orig_line to
        # None so that any following code is NOT attributed to our file.
        active_orig_lines: set = set()
        current_orig_line: Optional[int] = None
        cleaned_lines: List[str] = []
        for line in preprocessed.splitlines():
            m = _LINE_MARKER_RE.match(line)
            if m:
                ln = int(m.group(1))
                marker_file = m.group(2)
                if os.path.realpath(marker_file) == os.path.realpath(file_path):
                    current_orig_line = ln
                else:
                    # We are now inside a different file (header).
                    current_orig_line = None
                # Drop the marker from output.
                continue
            # Regular preprocessed line. If we're inside our file, the
            # contiguous block of non-marker lines comes from this
            # original line — mark it active.
            if current_orig_line is not None and line.strip():
                active_orig_lines.add(current_orig_line)
            cleaned_lines.append(line)

        # If clang expanded a section that spans multiple original lines
        # but only emitted one line marker (e.g. a block of consecutive
        # active lines), we conservatively mark ONLY the marker line as
        # active. False negatives are acceptable for the oracle use
        # case; false positives would mislead the LLM.
        cleaned = "\n".join(cleaned_lines)
        pruned_lines = sum(1 for l in cleaned_lines if l.strip())

        # The skipped_ranges complement: lines in original NOT in
        # active_orig_lines. We compute it as 1-indexed inclusive
        # ranges of contiguous inactive regions.
        skipped: List[Tuple[int, int]] = []
        in_skip = False
        start = 0
        for ln in range(1, original_lines + 1):
            if ln not in active_orig_lines:
                if not in_skip:
                    start = ln
                    in_skip = True
            else:
                if in_skip:
                    skipped.append((start, ln - 1))
                    in_skip = False
        if in_skip:
            skipped.append((start, original_lines))

        # Banner telling the LLM this is the oracle output, not the
        # usual regex-pruned code.
        banner = (
            "/* ── MacroPruner-Ctx (Clang Backend / Oracle) ────── */\n"
            f"/* Target: {target}                                   */\n"
            f"/* Note: Fully preprocessed by clang -E.             */\n"
            f"/* Macros expanded, #include'd content inlined.       */\n"
            f"/* Use this output for CROSS-VALIDATION only —        */\n"
            f"/* prefer the regex backend for normal LLM reading.  */\n"
            f"/* Active original lines: {len(active_orig_lines):>5}                */\n"
            f"/* Skipped ranges: {len(skipped):>5}                       */\n"
            "/* ─────────────────────────────────────────────────── */\n\n"
        )

        return PruneResult(
            code=banner + cleaned,
            skipped_ranges=skipped,
            original_lines=original_lines,
            pruned_lines=pruned_lines,
            backend_name="clang",
            original_code=original_source,
            extra={
                "clang_path": self._clang_path,
                "active_line_count": str(len(active_orig_lines)),
                "oracle": "true",
            },
        )
