"""RegexBackend — default pure-Python backend. Wraps PrunerCore.

Output preserves original C structure: macros NOT expanded, includes
NOT inlined. This is what an LLM wants for reading the code.
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

from cc_parser import CompileDBParser
from pruner_core import PrunerCore, PrunerMode

from .base import PruneResult, PrunerBackend, register_backend


@register_backend
class RegexBackend(PrunerBackend):
    """Pure-Python backend using PrunerCore + ExpressionEvaluator.

    Always available (no external tools needed).
    """

    name = "regex"

    def __init__(self):
        pass

    def is_available(self) -> Tuple[bool, str]:
        return True, ""

    def prune(
        self,
        file_path: str,
        target: str,
        compile_db: str,
        mode: str = "physical",
    ) -> PruneResult:
        # Resolve file
        if not os.path.isabs(file_path):
            file_path = os.path.abspath(file_path)
        if not os.path.isfile(file_path):
            raise FileNotFoundError(file_path)

        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            original_source = f.read()
        original_lines = original_source.count("\n") + (0 if original_source.endswith("\n") else 1)

        # Build active_macros from target + compile_db.
        active_macros: Dict[str, Optional[str]] = {}
        target_upper = target.upper()
        active_macros[target_upper] = None
        active_macros[f"TARGET_{target_upper}"] = None
        active_macros[f"PRODUCT_{target_upper}"] = None

        try:
            parser = CompileDBParser(compile_db)
            db_macros = parser.extract_macros(file_path)
            active_macros.update(db_macros)
        except Exception:
            pass

        pruner_mode = (
            PrunerMode.VIRTUAL_FOLDING if mode == "virtual"
            else PrunerMode.PHYSICAL_DELETION
        )

        pruner = PrunerCore(active_macros=active_macros, mode=pruner_mode)
        pruned = pruner.prune(original_source)
        pruned_lines = sum(1 for l in pruned.splitlines() if l.strip())

        return PruneResult(
            code=pruned,
            skipped_ranges=pruner.skipped_ranges,
            original_lines=original_lines,
            pruned_lines=pruned_lines,
            backend_name="regex",
            original_code=original_source,
        )
