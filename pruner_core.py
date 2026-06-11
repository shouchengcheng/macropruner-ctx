"""
Conditional Compilation Pruner - Stack-based state machine for handling nested preprocessor directives.
Prunes inactive code blocks based on active macro definitions while preserving line numbers.
"""

import re
from enum import Enum, auto
from typing import List, Dict, Optional


class BlockState(Enum):
    ACTIVE = auto()
    INACTIVE = auto()


class PrunerMode(Enum):
    PHYSICAL_DELETION = "physical"
    VIRTUAL_FOLDING = "virtual"


class ConditionalBlock:
    """Represents a single conditional compilation block (#ifdef/#ifndef/#else/#endif)."""

    def __init__(self, directive: str, condition: str, is_active: bool):
        self.directive = directive  # ifdef, ifndef, else, elif
        self.condition = condition
        self.is_active = is_active
        self.state = BlockState.ACTIVE if is_active else BlockState.INACTIVE
        self.has_else = False


class PrunerCore:
    """
    Stack-based state machine that prunes inactive conditional compilation code.

    Handles deeply nested #ifdef/#ifndef/#else/#elif/#endif blocks by maintaining
    a stack of ConditionalBlock states. Only code in fully-active paths is kept.
    """

    DIRECTIVE_PATTERN = re.compile(
        r"^\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b\s*(.*)", re.MULTILINE
    )

    def __init__(
        self,
        active_macros: Dict[str, Optional[str]],
        mode: PrunerMode = PrunerMode.VIRTUAL_FOLDING,
    ):
        self.active_macros = active_macros
        self.mode = mode
        self.stack: List[ConditionalBlock] = []
        self.output_lines: List[str] = []
        self.skipped_ranges: List[tuple] = []
        self.current_skip_start: Optional[int] = None

    def evaluate_condition(self, condition: str) -> bool:
        """Evaluate whether a preprocessor condition is true given active macros."""
        condition = condition.strip()

        if not condition:
            return False

        if "defined(" in condition or "defined " in condition:
            match = re.search(r"defined\s*\(\s*(\w+)\s*\)|defined\s+(\w+)", condition)
            if match:
                macro_name = match.group(1) or match.group(2)
                return macro_name in self.active_macros

        simple_match = re.match(r"(\w+)", condition)
        if simple_match:
            macro_name = simple_match.group(1)
            return macro_name in self.active_macros

        return False

    def is_currently_active(self) -> bool:
        """Check if we're currently in an active code path (all ancestors are active)."""
        return all(block.state == BlockState.ACTIVE for block in self.stack)

    def process_line(self, line: str, line_num: int) -> str:
        """Process a single line, handling directives and pruning inactive code."""
        directive_match = self.DIRECTIVE_PATTERN.match(line)

        if directive_match:
            directive = directive_match.group(1)
            condition = directive_match.group(2).strip()

            if directive in ("ifdef", "ifndef"):
                return self._handle_if(directive, condition, line, line_num)
            elif directive == "elif":
                return self._handle_elif(condition, line, line_num)
            elif directive == "else":
                return self._handle_else(line, line_num)
            elif directive == "endif":
                return self._handle_endif(line, line_num)

        if self.is_currently_active():
            return line
        else:
            if self.mode == PrunerMode.PHYSICAL_DELETION:
                return None
            else:
                return ""

    def _handle_if(self, directive: str, condition: str, line: str, line_num: int) -> str:
        """Handle #ifdef or #ifndef directive."""
        is_condition_true = self.evaluate_condition(condition)

        if directive == "ifndef":
            is_condition_true = not is_condition_true

        parent_active = self.is_currently_active() if self.stack else True
        is_active = parent_active and is_condition_true

        block = ConditionalBlock(directive, condition, is_active)
        self.stack.append(block)

        if self.mode == PrunerMode.VIRTUAL_FOLDING:
            comment_marker = f"/* [{directive.upper()} {condition} - {'ACTIVE' if is_active else 'INACTIVE'}] */"
            return comment_marker
        return None

    def _handle_elif(self, condition: str, line: str, line_num: int) -> str:
        """Handle #elif directive."""
        if not self.stack:
            return line

        top_block = self.stack[-1]

        if top_block.directive not in ("ifdef", "ifndef", "elif"):
            return line

        parent_active = all(b.state == BlockState.ACTIVE for b in self.stack[:-1])
        previous_was_inactive = top_block.state == BlockState.INACTIVE

        is_condition_true = self.evaluate_condition(condition)
        is_active = parent_active and previous_was_inactive and is_condition_true

        if previous_was_inactive:
            top_block.state = BlockState.ACTIVE if is_active else BlockState.INACTIVE
            top_block.directive = "elif"
            top_block.condition = condition
        else:
            top_block.state = BlockState.INACTIVE

        if self.mode == PrunerMode.VIRTUAL_FOLDING:
            status = "ACTIVE" if self.is_currently_active() else "INACTIVE"
            return f"/* [ELIF {condition} - {status}] */"
        return None

    def _handle_else(self, line: str, line_num: int) -> str:
        """Handle #else directive."""
        if not self.stack:
            return line

        top_block = self.stack[-1]

        if top_block.directive not in ("ifdef", "ifndef", "elif"):
            return line

        parent_active = all(b.state == BlockState.ACTIVE for b in self.stack[:-1])
        was_inactive = top_block.state == BlockState.INACTIVE

        top_block.has_else = True
        top_block.state = (
            BlockState.ACTIVE
            if (parent_active and was_inactive)
            else BlockState.INACTIVE
        )

        if self.mode == PrunerMode.VIRTUAL_FOLDING:
            status = "ACTIVE" if self.is_currently_active() else "INACTIVE"
            return f"/* [ELSE - {status}] */"
        return None

    def _handle_endif(self, line: str, line_num: int) -> str:
        """Handle #endif directive."""
        if not self.stack:
            return line

        closed_block = self.stack.pop()

        if self.mode == PrunerMode.VIRTUAL_FOLDING:
            return f"/* [ENDIF {closed_block.directive}] */"
        return None

    def prune(self, source_code: str) -> str:
        """
        Main entry point: prune inactive code from source based on active macros.

        Args:
            source_code: Raw C/C++ source code text

        Returns:
            Pruned source code with inactive blocks removed or folded
        """
        lines = source_code.splitlines()
        processed_lines = []

        for line_num, line in enumerate(lines, 1):
            result = self.process_line(line, line_num)
            if result is not None:
                processed_lines.append(result)

        if self.stack:
            unclosed = ", ".join(b.directive for b in self.stack)
            raise ValueError(f"Unclosed conditional directives: {unclosed}")

        return "\n".join(processed_lines)

    def get_pruning_stats(self, original: str, pruned: str) -> Dict:
        """Generate statistics about the pruning operation."""
        original_lines = original.splitlines()
        pruned_lines = [l for l in pruned.splitlines() if l.strip()]

        original_count = len(original_lines)
        pruned_count = len(pruned_lines)
        removed_count = original_count - pruned_count
        reduction_pct = (
            (removed_count / original_count * 100) if original_count > 0 else 0
        )

        return {
            "original_lines": original_count,
            "pruned_lines": pruned_count,
            "removed_lines": removed_count,
            "reduction_percentage": round(reduction_pct, 2),
            "mode": self.mode.value,
        }


def prune_source(
    source_code: str,
    active_macros: Dict[str, Optional[str]],
    mode: PrunerMode = PrunerMode.VIRTUAL_FOLDING,
) -> tuple:
    """
    Convenience function to prune source code and return results.

    Args:
        source_code: Raw C/C++ source code
        active_macros: Dictionary of active macro definitions
        mode: Pruning mode (PHYSICAL_DELETION or VIRTUAL_FOLDING)

    Returns:
        Tuple of (pruned_code, stats_dict)
    """
    pruner = PrunerCore(active_macros=active_macros, mode=mode)
    pruned = pruner.prune(source_code)
    stats = pruner.get_pruning_stats(source_code, pruned)
    return pruned, stats
