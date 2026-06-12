"""
Conditional Compilation Pruner - Stack-based state machine for handling nested preprocessor directives.
Prunes inactive code blocks based on active macro definitions while preserving line numbers.
"""

import re
from enum import Enum, auto
from typing import List, Dict, Optional

from expr_eval import ExpressionEvaluator


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
        # True once any branch in this if/elif chain has been taken.
        # Used to suppress all subsequent #elif/#else branches after a
        # match (per C preprocessor semantics: at most one branch of an
        # if/elif chain is active).
        self.taken = is_active


class PrunerCore:
    """
    Stack-based state machine that prunes inactive conditional compilation code.

    Handles deeply nested #ifdef/#ifndef/#else/#elif/#endif blocks by maintaining
    a stack of ConditionalBlock states. Only code in fully-active paths is kept.

    Condition evaluation is delegated to `ExpressionEvaluator`, which understands:
      - `defined(X)` and `defined X` (both forms)
      - `MACRO == N` / `!= N` / `<` / `>` / `<=` / `>=` with numeric values
      - `&&` / `||` / `!` / parentheses
      - Linux-style `IS_ENABLED(CONFIG_X)` (whitelisted macro expansion)
      - Case-insensitive identifier matching
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
        self._evaluator = ExpressionEvaluator(active_macros)
        self.stack: List[ConditionalBlock] = []
        self.output_lines: List[str] = []
        self.skipped_ranges: List[tuple] = []
        self.current_skip_start: Optional[int] = None

    def evaluate_condition(self, condition: str) -> bool:
        """Evaluate whether a preprocessor condition is true given active macros.

        Delegates to ExpressionEvaluator, which handles the full grammar:
        defined(), logical ops, comparisons, IS_ENABLED(), case-insensitive
        identifiers, numeric macro values.

        Backward-compat note: this method returns bool, but the internal
        parser uses 1/0 for bare identifiers. A 0 evaluates to False here.

        Failure modes (all fall back to False, treating the block as inactive):
          - ValueError: parser rejection (unbalanced parens, unsupported construct)
          - IndexError: parser walked past end of token stream (e.g. truncated
            condition from a `\` line-continuation we didn't preprocess). This
            used to bubble all the way out of `prune()` and crash the whole
            call, leaving every subsequent line un-pruned. Catching it here
            matches the historical "best-effort, never crash" contract.
        """
        try:
            result = self._evaluator.evaluate(condition)
            return bool(result)
        except (ValueError, IndexError):
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

            if directive in ("ifdef", "ifndef", "if"):
                return self._handle_if(directive, condition, line, line_num)
            elif directive == "elif":
                return self._handle_elif(condition, line, line_num)
            elif directive == "else":
                return self._handle_else(line, line_num)
            elif directive == "endif":
                return self._handle_endif(line, line_num)

        if self.is_currently_active():
            # Closing any open skip range: we are inside active code now.
            if self.current_skip_start is not None:
                self.skipped_ranges.append(
                    (self.current_skip_start, line_num - 1)
                )
                self.current_skip_start = None
            return line
        else:
            # Starting a new skip range if not already in one.
            if self.current_skip_start is None:
                self.current_skip_start = line_num
            if self.mode == PrunerMode.PHYSICAL_DELETION:
                return None
            else:
                return ""

    def _handle_if(self, directive: str, condition: str, line: str, line_num: int) -> str:
        """Handle #ifdef, #ifndef, or #if directive.

        For #if, the condition is a full C preprocessor expression evaluated
        by ExpressionEvaluator. For #ifdef / #ifndef, the condition is just
        a single identifier.
        """
        if directive == "ifndef":
            is_condition_true = not self.evaluate_condition(condition)
        else:
            is_condition_true = self.evaluate_condition(condition)

        parent_active = self.is_currently_active() if self.stack else True
        is_active = parent_active and is_condition_true

        block = ConditionalBlock(directive, condition, is_active)
        self.stack.append(block)

        if self.mode == PrunerMode.VIRTUAL_FOLDING:
            comment_marker = f"/* [{directive.upper()} {condition} - {'ACTIVE' if is_active else 'INACTIVE'}] */"
            return comment_marker
        return None

    def _handle_elif(self, condition: str, line: str, line_num: int) -> str:
        """Handle #elif directive.

        Semantics: this #elif is active iff (a) the chain hasn't already
        been taken by an earlier branch, AND (b) its condition is true,
        AND (c) all ancestor blocks are active.
        """
        if not self.stack:
            return line

        top_block = self.stack[-1]

        if top_block.directive not in ("ifdef", "ifndef", "if", "elif"):
            return line

        parent_active = all(b.state == BlockState.ACTIVE for b in self.stack[:-1])

        if top_block.taken:
            # Some earlier branch already won. This elif is inactive
            # regardless of its condition.
            new_active = False
        else:
            new_active = parent_active and self.evaluate_condition(condition)

        top_block.state = BlockState.ACTIVE if new_active else BlockState.INACTIVE
        if new_active:
            top_block.taken = True
        top_block.directive = "elif"
        top_block.condition = condition

        if self.mode == PrunerMode.VIRTUAL_FOLDING:
            status = "ACTIVE" if self.is_currently_active() else "INACTIVE"
            return f"/* [ELIF {condition} - {status}] */"
        return None

    def _handle_else(self, line: str, line_num: int) -> str:
        """Handle #else directive.

        The #else branch is active iff (a) the chain hasn't been taken
        yet, AND (b) all ancestors are active.
        """
        if not self.stack:
            return line

        top_block = self.stack[-1]

        if top_block.directive not in ("ifdef", "ifndef", "if", "elif"):
            return line

        parent_active = all(b.state == BlockState.ACTIVE for b in self.stack[:-1])
        new_active = parent_active and not top_block.taken

        top_block.has_else = True
        top_block.state = BlockState.ACTIVE if new_active else BlockState.INACTIVE
        if new_active:
            top_block.taken = True

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

        Behavior on unclosed conditionals:
            If the source has unclosed #if/#ifdef blocks (e.g. an `#if 0`
            block with no matching `#endif`), the pruner does NOT raise.
            It appends a warning comment to the output and treats the
            remaining stack as a non-fatal condition. This matches the
            historical "don't crash on weird code" behavior expected by
            the unbalanced_real_world test case.
        """
        import warnings
        lines = source_code.splitlines()
        processed_lines = []

        for line_num, line in enumerate(lines, 1):
            result = self.process_line(line, line_num)
            if result is not None:
                processed_lines.append(result)

        if self.stack:
            unclosed = ", ".join(b.directive for b in self.stack)
            warnings.warn(f"Unclosed conditional directives: {unclosed}")
            if self.mode == PrunerMode.VIRTUAL_FOLDING:
                processed_lines.append(
                    f"/* [WARN: unclosed directives: {unclosed}] */"
                )

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
