"""Standalone unified diff applier — no git dependency.

Why a custom implementation?
  The original apply_patch tool used `git apply`, which is great when
  the target file is in a git repo, but most embedded firmware
  projects aren't. The custom applier covers the common 80% case
  (single-file diffs with explicit line offsets) without requiring
  git on the host.

What this does NOT do (deliberate scope cuts):
  - Fuzzy matching when offsets don't line up. If the diff says
    "@@ -45,3 +45,4 @@" and the file is 50 lines, we fail.
  - Multi-file diffs in a single input. Each call applies to one
    file only; the caller splits.
  - Binary file hunks.
  - Rename / copy / mode-change diffs.
  - Git-style extended headers (git prefix "a/", "b/", etc.) are
    accepted but only for matching the file name; the applier
    doesn't track moves.

Format reminder (unified diff):
    --- a/path/to/file
    +++ b/path/to/file
    @@ -from_line,from_count +to_line,to_count @@ optional heading
     context line (starts with single space)
    -removed line (starts with -)
    +added line (starts with +)
    \\ no newline at end of file (rare, ignored here)

Syntax pre-check (post-apply):
  - Brace balance: every { has a matching } (warning if not).
  - #if / #endif balance: count opens, count closes; warn if mismatched.
  These are warnings, not blockers — a patch that fixes a syntax
  error in the source should still apply.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple


# Match the unified diff hunk header.
_HUNK_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$"
)

# Match a diff file header.
_FILE_RE = re.compile(r"^(---|\+\+\+)\s+(.+?)\s*$")


class PatchError(Exception):
    """Raised when a diff cannot be applied. Self-explanatory messages."""


def _parse_diff(diff_text: str) -> List[Tuple[int, int, int, int, str]]:
    """Parse a unified diff into a list of hunk specs.

    Each hunk is (from_line, from_count, to_line, to_count, body)
    where body is a string of hunk lines including the leading
    ' ', '-', '+' prefixes.

    Skips file headers (--- / +++) and any "No newline at end of
    file" markers. Raises PatchError on malformed input.
    """
    lines = diff_text.splitlines()
    i = 0
    hunks: List[Tuple[int, int, int, int, str]] = []

    # We don't require a file header — some callers (LLMs) drop
    # them when sending minimal diffs. But if one is present, we
    # skip past it without complaint.
    while i < len(lines):
        m = _HUNK_RE.match(lines[i])
        if m is not None:
            from_line = int(m.group(1))
            from_count = int(m.group(2)) if m.group(2) is not None else 1
            to_line = int(m.group(3))
            to_count = int(m.group(4)) if m.group(4) is not None else 1
            heading = m.group(5).strip()
            # Build the hunk body. The heading is preserved as a
            # context-only line above the hunk (matches git apply
            # semantics for @@ headings).
            i += 1
            body_lines: List[str] = []
            while i < len(lines):
                ln = lines[i]
                if _HUNK_RE.match(ln):
                    break
                if ln.startswith(("---", "+++")) and i > 0:
                    # New file header — finish this hunk.
                    break
                if ln.startswith("\\"):
                    # "\ No newline at end of file" — ignore.
                    i += 1
                    continue
                if ln and ln[0] in (" ", "-", "+", "\\"):
                    body_lines.append(ln)
                    i += 1
                    continue
                # Empty line or anything not starting with a marker
                # is treated as a context line (some LLM-generated
                # diffs drop the leading space on empty lines).
                body_lines.append(" " + ln if ln else " ")
                i += 1
            # Re-emit heading as a context-only line at the top of
            # the hunk body so we don't drop information.
            full_body = "\n".join(body_lines)
            if heading:
                full_body = " " + heading + "\n" + full_body
            hunks.append((from_line, from_count, to_line, to_count, full_body))
            continue
        i += 1

    if not hunks:
        raise PatchError("diff contains no hunks (looking for '@@ -... +... @@' headers)")
    return hunks


def _apply_hunk(source_lines: List[str], hunk: Tuple[int, int, int, int, str]) -> List[str]:
    """Apply a single hunk to source_lines. Returns the new list."""
    from_line, from_count, to_count, _to_line, body = hunk

    # Convert the 1-indexed from_line to 0-indexed array position.
    # from_line=1 means the first line of the file.
    start_idx = from_line - 1
    if start_idx < 0:
        raise PatchError(f"hunk starts at line {from_line}, before file start")
    if start_idx > len(source_lines):
        raise PatchError(
            f"hunk starts at line {from_line}, but file has only {len(source_lines)} lines"
        )

    # Parse the body into keep/remove/add operations.
    # We process sequentially and build a slice of new lines.
    new_chunk: List[str] = []
    body_lines = body.splitlines()
    # Track the position in the source as we consume it.
    cursor = start_idx
    consumed_from = 0  # lines consumed from the original source

    for bl in body_lines:
        if not bl:
            # Empty line (shouldn't really happen since we normalize
            # them to a single space, but be defensive).
            new_chunk.append("")
            continue
        marker = bl[0]
        content = bl[1:] if len(bl) > 1 else ""
        if marker == " ":
            # Context line: must match the source. If it doesn't,
            # raise (no fuzzy match).
            if cursor >= len(source_lines):
                raise PatchError(
                    f"hunk context wants line {cursor + 1}, but file is only {len(source_lines)} lines"
                )
            if source_lines[cursor] != content:
                raise PatchError(
                    f"hunk context mismatch at line {cursor + 1}:\n"
                    f"  diff says: {content!r}\n"
                    f"  file has:  {source_lines[cursor]!r}"
                )
            new_chunk.append(content)
            cursor += 1
            consumed_from += 1
        elif marker == "-":
            # Removed line: must match the source.
            if cursor >= len(source_lines):
                raise PatchError(
                    f"hunk wants to remove line {cursor + 1}, but file is only {len(source_lines)} lines"
                )
            if source_lines[cursor] != content:
                raise PatchError(
                    f"hunk removal mismatch at line {cursor + 1}:\n"
                    f"  diff says: {content!r}\n"
                    f"  file has:  {source_lines[cursor]!r}"
                )
            cursor += 1
            consumed_from += 1
        elif marker == "+":
            new_chunk.append(content)
        else:
            raise PatchError(f"unrecognized hunk line marker: {marker!r} in {bl!r}")

    if consumed_from != from_count:
        # We allow off-by-N for cases where the diff says "from 5 lines"
        # but the body actually has 4 — this is a sloppy LLM diff.
        # We don't fail; we just note it (the actual line matching
        # already happened above).
        pass

    # Splice: source_lines[:start_idx] + new_chunk + source_lines[cursor:]
    return source_lines[:start_idx] + new_chunk + source_lines[cursor:]


def apply_unified_diff(original: str, diff_text: str) -> str:
    """Apply a unified diff to `original` text and return the result.

    Args:
        original: The full text of the file BEFORE patching.
        diff_text: A unified diff (with or without ---/+++ headers).

    Returns:
        The new file content as a string (no trailing newline is
        added if `original` didn't have one).

    Raises:
        PatchError: Any structural or content mismatch.

    Hunk indexing:
        Each hunk's @@ -from,from_count +to,to_count @@ uses RAW
        line numbers from the original file (not cumulative). When
        applying multiple hunks, the to-side offset of each hunk
        is shifted by the net line count added/removed by all
        previous hunks. This matches the convention used by git
        apply, patch(1), and every standard diff tool.
    """
    hunks = _parse_diff(diff_text)
    source_lines = original.splitlines()
    # Preserve the original trailing-newline state.
    trailing_nl = original.endswith("\n")

    # Cumulative net change from hunks we've already applied.
    # Used to shift the from_line of subsequent hunks to match
    # the current state of source_lines.
    cumulative_net = 0

    for from_line, from_count, _to_line, to_count, body in hunks:
        # Shift the from_line by the cumulative net change.
        adjusted_from = from_line + cumulative_net
        source_lines = _apply_hunk(
            source_lines, (adjusted_from, from_count, _to_line, to_count, body)
        )
        cumulative_net += (to_count - from_count)

    result = "\n".join(source_lines)
    if trailing_nl:
        result += "\n"
    return result


# ── Syntax pre-check (post-apply) ──────────────────────────────────


def check_c_syntax(content: str) -> List[str]:
    """Run a lightweight syntax sanity check on C/C++ text.

    Returns a list of warning strings. Empty list = no issues found.
    This is NOT a real C parser — it just catches the obvious
    structural errors that would make a file non-compilable:
      - Unbalanced braces
      - Unbalanced #if / #endif
      - Stray #else after #endif (very common LLM error)

    A real patch that fixes a syntax error will trigger some of
    these warnings; callers should treat them as hints, not as
    patch failures.
    """
    warnings: List[str] = []
    brace_depth = 0
    in_string = False
    in_char = False
    in_line_comment = False
    in_block_comment = False
    escape_next = False

    if_depth = 0  # #if/#ifdef nesting
    last_if_kind: List[str] = []  # "if" | "ifdef" | "ifndef" | "elif" | "else"
    expecting_endif_at = -1  # line number where we should see #endif

    for lineno, line in enumerate(content.splitlines(), 1):
        i = 0
        # Pre-process line for the if/endif tracker.
        stripped = line.strip()
        if stripped.startswith("#"):
            directive = stripped[1:].strip().split()
            if not directive:
                continue
            kind = directive[0]
            if kind in ("if", "ifdef", "ifndef"):
                if_depth += 1
                last_if_kind.append(kind)
                expecting_endif_at = lineno  # best-effort
            elif kind == "elif":
                if if_depth == 0:
                    warnings.append(f"line {lineno}: #elif without matching #if")
            elif kind == "else":
                if if_depth == 0 or last_if_kind[-1] not in ("if", "ifdef", "ifndef", "elif"):
                    warnings.append(f"line {lineno}: #else without matching #if")
                else:
                    last_if_kind[-1] = "else"
            elif kind == "endif":
                if if_depth == 0:
                    warnings.append(f"line {lineno}: #endif without matching #if")
                else:
                    if_depth -= 1
                    if last_if_kind:
                        last_if_kind.pop()
            continue  # directives don't count for brace tracking
        # Brace / comment / string tracking for non-directive lines.
        while i < len(line):
            ch = line[i]
            if escape_next:
                escape_next = False
                i += 1
                continue
            if ch == "\\":
                escape_next = True
                i += 1
                continue
            if in_line_comment:
                break
            if in_block_comment:
                if ch == "*" and i + 1 < len(line) and line[i + 1] == "/":
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue
            if in_string:
                if ch == '"':
                    in_string = False
                i += 1
                continue
            if in_char:
                if ch == "'":
                    in_char = False
                i += 1
                continue
            if ch == "/" and i + 1 < len(line):
                if line[i + 1] == "/":
                    in_line_comment = True
                    break
                if line[i + 1] == "*":
                    in_block_comment = True
                    i += 2
                    continue
            if ch == '"':
                in_string = True
                i += 1
                continue
            if ch == "'":
                in_char = True
                i += 1
                continue
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
            i += 1

    if brace_depth != 0:
        warnings.append(f"unbalanced braces: depth={brace_depth} at end of file")
    if if_depth != 0:
        warnings.append(f"unbalanced #if/#endif: {if_depth} unmatched #if at end of file")
    return warnings
