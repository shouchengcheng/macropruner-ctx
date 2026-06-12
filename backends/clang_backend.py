"""ClangBackend — invokes the real clang preprocessor for ground-truth
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

Cross-compile SDK support (P4-1):
  In v0.4 the backend had no awareness of the SDK's toolchain — running
  it against a HiSilicon ws63 SDK (riscv32-linux-musl) failed with
  "fatal error: 'port/header.h' file not found" because clang's
  default sysroot has no idea where the SDK's cross-compile headers
  live.

  P4-1 fixes this by:
    1. Reading the project's compile_db entry's full token list
       and reusing the compiler flags the build system already knows
       work (--target, -march, -mabi, -isystem, etc.).
    2. Letting the caller pass an explicit `sysroot` (CLI flag,
       MCP param, or .macroprunerrc key) to override.
    3. Auto-detecting --target= and --sysroot= from the compile_db
       command when the user didn't pass one.
    4. Sanitizing gcc-specific flags clang doesn't understand
       (filter -fno-tree-loop-distribute-patterns, etc.).

  The result: `cli.py read main.c --backend clang` works against
  riscv32-linux-musl, aarch64-linux-gnu, and any other clang-
  supported cross SDK without manual flag wrangling.

Implementation notes
---------------------
We invoke `clang -E -dD -w` on the file with the compile_db's -D and
-I flags. The output preserves `# N "file"` line markers, which we
use to:
  1. Track which original-file lines survived preprocessing (= active).
  2. Strip the markers from the returned code (LLM should not see them).

We deliberately do NOT call `clang -cc1 -ast-dump` or any AST-level
API — that requires the libclang Python binding (not installed in
the target environments this project supports). Plain `clang -E` is
enough for the cross-validation use case and is universally available.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from typing import Dict, List, Optional, Set, Tuple

from cc_parser import CompileDBParser

from .base import PruneResult, PrunerBackend, register_backend


_CLANG_CANDIDATES = ("clang", "clang-14", "clang-13", "clang-12", "clang-11", "clang-10")

# `# N "file"` or `# N "file" N2` markers emitted by clang -E.
_LINE_MARKER_RE = re.compile(r'^\s*#\s+(\d+)\s+"([^"]+)"')

# Compiler flags clang understands and is happy to inherit from a
# project whose build is normally done with gcc. These are the
# common cross-compile flags that we WANT to pass through.
#
# Anything not in this allow-list is dropped (or replaced with
# nothing) so that gcc-specific flags (-fno-tree-loop-distribute-patterns,
# -Wl,--no-undefined, etc.) don't confuse clang.
_CLANG_FLAG_ALLOWLIST_PREFIXES = (
    "-D",
    "-I",
    "-isystem",
    "-iquote",
    "-include",
    "--target=",
    "--sysroot=",
    "-march=",
    "-mcpu=",
    "-mabi=",
    "-mfloat-abi=",
    "-mthumb",
    "-marm",
    "-f",
    "-std=",
    "-W",          # warnings; clang is OK with most gcc warnings
    "-w",          # suppress warnings (we use this ourselves)
    "-no-canonical-prefixes",
    "-pipe",
    "-pthread",
)

# Flags that should be passed but with their value kept as-is.
_CLANG_FLAG_ALLOWLIST_EXACT = {
    "-E", "-c", "-S",            # we override -E but the others are inert
    "-shared", "-static",         # link flags; harmless
    "-nostdinc", "-nostdinc++",  # include control
    "-undef",                     # we may want this for preprocessing
    "-x",                        # followed by "c" or "c++"
    "-Xclang",                   # for very rare cases
}

# Compiler-specific flags to drop (gcc/clang extensions that don't
# translate, or driver flags like -Wl that only matter at link time).
_DROP_PREFIXES = (
    "-Wl,",                      # linker flags
    "-Wa,",                      # assembler flags
    "-Werror",                   # treat warnings as errors (we already suppress)
    "-save-temps",                # debug
    "-ftime-report",              # debug
    "--param=",                  # gcc optimization
    "-z",                        # linker
    "-static-libgcc", "-static-libstdc++",
    "-nodefaultlibs", "-nolibc",
    # Catch-all: drop ALL -f flags. Clang supports a small subset
    # of gcc -f flags (e.g. -fno-rtti, -fno-exceptions,
    # -fno-builtin, -fno-stack-protector); the vast majority of
    # gcc's -f optimization flags (RISC-V tuning, tree-distribute,
    # etc.) are unknown to clang. None of these affect the
    # preprocessing output (the result of `clang -E` is the
    # same with or without them), so dropping them all is safe.
    "-f",
)


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


def _filter_tokens_for_clang(tokens: List[str]) -> List[str]:
    """Filter a token list (typically from a gcc command) down to what
    clang can safely consume.

    Removes link flags, gcc-specific optimization flags, and any
    flag that has a strong chance of being unknown to clang.

    For flags that take a value as the next token (e.g. -x c, -D X=1
    where the = is the value, -I <path>), the value is kept together
    with the flag so the resulting list is still a valid clang argv.

    Returns a NEW list; the input is unmodified.
    """
    # Flags whose value is the immediately following token (not
    # attached via '='). When we see one of these, the NEXT token is
    # treated as its value, regardless of whether that value would
    # otherwise be dropped on its own.
    _VALUE_AS_NEXT = {
        "-I", "-isystem", "-iquote", "-include", "-x",
    }

    out: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # Drop whole-prefix families.
        if any(tok.startswith(p) for p in _DROP_PREFIXES):
            i += 1
            continue

        # Keep whole-prefix families we DO want. Some of these
        # (-I, -isystem, -iquote, -include) take their value as the
        # next token, so consume it too.
        prefix_match = next(
            (p for p in _CLANG_FLAG_ALLOWLIST_PREFIXES if tok.startswith(p)),
            None,
        )
        if prefix_match is not None:
            out.append(tok)
            i += 1
            if prefix_match in _VALUE_AS_NEXT and i < len(tokens):
                out.append(tokens[i])
                i += 1
            continue

        # Keep exact-match flags; consume their value as the next
        # token if applicable.
        if tok in _CLANG_FLAG_ALLOWLIST_EXACT:
            out.append(tok)
            i += 1
            if tok in _VALUE_AS_NEXT and i < len(tokens):
                out.append(tokens[i])
                i += 1
            continue

        # Drop everything else (compiler-specific, link flags that
        # already slipped past the prefix filter, etc.).
        i += 1
    return out


@register_backend
class ClangBackend(PrunerBackend):
    """Backend that runs the actual clang preprocessor.

    Supports cross-compile SDKs via the `sysroot` and `extra_target`
    constructor parameters (or the equivalent fields on the MCP tool
    / CLI / .macroprunerrc).

    The output is fully preprocessed code. It is not a drop-in for
    regex-backend output (which preserves the original C structure).
    """

    name = "clang"

    def __init__(
        self,
        timeout: float = 15.0,
        sysroot: Optional[str] = None,
        extra_target: Optional[str] = None,
    ):
        """Args:
            timeout: Max seconds to wait for a single clang invocation.
            sysroot: Path to the SDK's cross-compile sysroot. If None,
                     we auto-detect from the compile_db entry's command.
            extra_target: Optional --target= flag override (e.g.
                          "riscv32-linux-musl"). Auto-detected from
                          the compile_db if not given.
        """
        self._clang_path: Optional[str] = None
        self._timeout = timeout
        self._sysroot_override = sysroot
        self._extra_target = extra_target
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

        # ── Build the clang command ──────────────────────────────
        # The strategy: REUSE the project's compile_db entry's flags
        # (filtered through _filter_tokens_for_clang) so the
        # cross-compile flags the build system already chose
        # (--target, -march, etc.) flow through. Then add our own
        # -E -w, our synthesized target macro, and any user-supplied
        # sysroot/target overrides.
        cmd = [self._clang_path, "-E", "-w"]
        if _is_cpp(file_path):
            cmd.extend(["-x", "c++"])
        else:
            cmd.extend(["-x", "c"])

        # Look up the entry to inherit flags from. If the entry uses
        # an explicit `arguments` array, prefer that over `command`.
        inherited_target: Optional[str] = None
        inherited_sysroot: Optional[str] = None
        try:
            parser = CompileDBParser(compile_db)
            entry_tokens = parser.get_entry_tokens_for_file(file_path)
            if entry_tokens:
                # Filter the inherited token list (drop link flags,
                # gcc-specific stuff) and append to cmd.
                kept = _filter_tokens_for_clang(entry_tokens)
                # Also strip the source filename and any -o <output>
                # argument — clang -E writes to stdout, not a file.
                cleaned: List[str] = []
                skip = 0
                for j, t in enumerate(kept):
                    if skip > 0:
                        skip -= 1
                        continue
                    if t == "-o" or t == "--output":
                        skip = 1
                        continue
                    if t.endswith((".c", ".cpp", ".cc", ".cxx", ".C", ".h", ".hpp")) and os.path.isabs(t):
                        # Source file argument; we'll add our own at the end.
                        continue
                    if t.startswith("--target="):
                        inherited_target = t.split("=", 1)[1]
                    if t.startswith("--sysroot="):
                        inherited_sysroot = t.split("=", 1)[1]
                    cleaned.append(t)
                # Don't re-add -D / -I here — we synthesize those from
                # the structured macro/include extractors below. The
                # point of inheriting flags is to get --target, -march,
                # -mabi, -isystem, etc. that we can't otherwise know.
                # Drop the -D / -I duplicates from the inherited set
                # to avoid double-counting.
                cleaned = [
                    t for t in cleaned
                    if not (t.startswith("-D") or t.startswith("-I")
                            or t.startswith("-isystem") or t.startswith("-iquote")
                            or t.startswith("-include"))
                ]
                cmd.extend(cleaned)

            # Pull -D and -I from the same entry via the structured
            # extractors (these are the canonical sources).
            macros = parser.extract_macros(file_path)
            include_dirs = parser.resolve_include_dirs(file_path)
        except Exception:
            macros = {}
            include_dirs = []

        # Decide which --target to use. Precedence:
        #   1. User-supplied extra_target (CLI/MCP param)
        #   2. --target= inherited from the compile_db entry
        #   3. None (clang's default target, fine for native builds)
        chosen_target = self._extra_target or inherited_target
        if chosen_target:
            cmd.append(f"--target={chosen_target}")

        # Decide which --sysroot to use. Precedence:
        #   1. User-supplied sysroot override
        #   2. --sysroot= inherited from the compile_db entry
        #   3. None (clang's default sysroot)
        chosen_sysroot = self._sysroot_override or inherited_sysroot
        if chosen_sysroot:
            cmd.append(f"--sysroot={chosen_sysroot}")

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

        # ── Run ────────────────────────────────────────────────
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"clang -E timed out after {self._timeout}s on {file_path}"
            ) from e
        if proc.returncode != 0:
            # Build a useful hint depending on what failed.
            hint = (
                "the file's compile_db entry's toolchain is unreachable by clang; "
                "pass --sysroot to point clang at the SDK's cross-compile sysroot, "
                "or set pruner.sysroot in .macroprunerrc"
            )
            if "cannot find" in proc.stderr or "file not found" in proc.stderr:
                # Standard "missing header" complaint — give the sysroot hint.
                raise RuntimeError(
                    f"clang -E failed (rc={proc.returncode}) on {file_path}: "
                    f"{proc.stderr[:500]}"
                ) from RuntimeError(hint)
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
            "/* --- MacroPruner-Ctx (Clang Backend / Oracle) --- */\n"
            f"/* Target:    {target:<36}*/\n"
            f"/* Backend:   {'auto-detect' if not chosen_sysroot else 'sysroot=' + chosen_sysroot:<36}*/\n"
            f"/* Note: Fully preprocessed by clang -E.             */\n"
            f"/* Macros expanded, #include'd content inlined.       */\n"
            f"/* Use this output for CROSS-VALIDATION only;         */\n"
            f"/* prefer the regex backend for normal LLM reading.  */\n"
            f"/* Active original lines: {len(active_orig_lines):>5}                */\n"
            f"/* Skipped ranges: {len(skipped):>5}                       */\n"
            "/* --------------------------------------------------- */\n\n"
        )

        return PruneResult(
            code=banner + "\n".join(cleaned_lines),
            skipped_ranges=skipped,
            original_lines=original_lines,
            pruned_lines=sum(1 for l in cleaned_lines if l.strip()),
            backend_name="clang",
            original_code=original_source,
            extra={
                "clang_path": self._clang_path or "",
                "active_line_count": str(len(active_orig_lines)),
                "oracle": "true",
                "inherited_target": inherited_target or "",
                "inherited_sysroot": inherited_sysroot or "",
                "effective_target": chosen_target or "",
                "effective_sysroot": chosen_sysroot or "",
            },
        )
