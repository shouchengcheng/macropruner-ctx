"""
C/C++ Code Skeletonizer - Strips function bodies while preserving declarations.

Keeps: struct/union/enum/typedef definitions, #define/#include directives,
       function signatures (return type + parameters).
Strips: Function body content between { ... }, replaced with { /* ... */ }
"""

import re
from typing import Dict


class Skeletonizer:
    FUNC_SIG_PATTERN = re.compile(r"^[\w\s\*]+?\b(\w+)\s*\([^;]*\)\s*$")

    DECL_KEYWORDS = {"struct", "union", "enum", "typedef", "extern"}

    PREPROCESSOR_PATTERN = re.compile(r"^\s*#")

    def __init__(self):
        self.stats: Dict[str, int] = {
            "functions_stripped": 0,
            "lines_removed": 0,
            "original_lines": 0,
            "skeleton_lines": 0,
        }

    def skeletonize(self, source_code: str) -> str:
        lines = source_code.splitlines()
        self.stats["original_lines"] = len(lines)
        output_lines = []
        i = 0

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if not stripped or self.PREPROCESSOR_PATTERN.match(stripped):
                output_lines.append(line)
                i += 1
                continue

            if self._is_declaration_block(stripped):
                block_end = self._find_matching_brace(lines, i)
                if block_end is not None:
                    for j in range(i, block_end + 1):
                        output_lines.append(lines[j])
                    i = block_end + 1
                    continue

            if self._looks_like_function_def(stripped, lines, i):
                brace_line = self._find_opening_brace(lines, i)
                if brace_line is not None:
                    sig_lines = []
                    for j in range(i, brace_line + 1):
                        sig_lines.append(lines[j].rstrip())

                    combined_sig = " ".join(s.strip() for s in sig_lines)
                    combined_sig = re.sub(r"\s+", " ", combined_sig).strip()
                    if combined_sig.endswith("{"):
                        combined_sig = combined_sig[:-1].rstrip()

                    output_lines.append(combined_sig)
                    output_lines.append("{ /* ... */ }")

                    body_end = self._find_matching_brace(lines, brace_line)
                    if body_end is not None:
                        body_lines = body_end - brace_line - 1
                        if body_lines > 0:
                            self.stats["lines_removed"] += body_lines
                        self.stats["functions_stripped"] += 1
                        i = body_end + 1
                    else:
                        i = brace_line + 1
                    continue

            output_lines.append(line)
            i += 1

        result = "\n".join(output_lines)
        self.stats["skeleton_lines"] = len([l for l in output_lines if l.strip()])
        return result

    def _is_declaration_block(self, stripped: str) -> bool:
        for kw in self.DECL_KEYWORDS:
            if stripped.startswith(kw + " ") or stripped.startswith(kw + "\t"):
                return True
        return False

    def _looks_like_function_def(self, stripped: str, lines: list, idx: int) -> bool:
        if (
            stripped.startswith("#")
            or stripped.startswith("//")
            or stripped.startswith("/*")
        ):
            return False

        if any(
            stripped.startswith(kw + " ")
            for kw in ("struct", "union", "enum", "typedef")
        ):
            return False

        lookahead = ""
        for j in range(idx, min(idx + 5, len(lines))):
            lookahead += lines[j].strip() + " "
            if "{" in lookahead:
                break

        if "(" not in lookahead:
            return False

        paren_depth = 0
        found_paren = False
        for ch in lookahead:
            if ch == "(":
                paren_depth += 1
                found_paren = True
            elif ch == ")":
                paren_depth -= 1
            elif ch == "{" and found_paren and paren_depth == 0:
                return True
            elif ch == ";" and found_paren and paren_depth == 0:
                return False

        return False

    def _find_opening_brace(self, lines: list, start: int) -> int:
        for i in range(start, min(start + 10, len(lines))):
            if "{" in lines[i]:
                return i
        return None

    def _find_matching_brace(self, lines: list, start: int) -> int:
        depth = 0
        in_string = False
        in_char = False
        escape_next = False

        for i in range(start, len(lines)):
            line = lines[i]
            j = 0
            while j < len(line):
                ch = line[j]

                if escape_next:
                    escape_next = False
                    j += 1
                    continue

                if ch == "\\":
                    escape_next = True
                    j += 1
                    continue

                if ch == '"' and not in_char:
                    in_string = not in_string
                elif ch == "'" and not in_string:
                    in_char = not in_char
                elif not in_string and not in_char:
                    if ch == "/" and j + 1 < len(line) and line[j + 1] == "/":
                        break
                    if ch == "/" and j + 1 < len(line) and line[j + 1] == "*":
                        end_comment = line.find("*/", j + 2)
                        if end_comment != -1:
                            j = end_comment + 2
                            continue
                        else:
                            break
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            return i

                j += 1

        return None

    def get_stats(self) -> Dict[str, int]:
        return dict(self.stats)


def skeletonize_source(source_code: str) -> tuple:
    skel = Skeletonizer()
    result = skel.skeletonize(source_code)
    return result, skel.get_stats()
