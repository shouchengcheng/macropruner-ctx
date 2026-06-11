"""Pruner backend abstraction.

A PrunerBackend takes a C/C++ source file + an active-macro dictionary
and produces a pruned source string (with inactive conditional compilation
blocks removed or folded).

Two concrete backends are shipped:

  - RegexBackend  (default, pure-Python, fast, covers ~95% of real code)
  - ClangBackend  (optional, invokes `clang -E`, produces ground-truth
                   active line sets + fully expanded code)

Backends are stateless. They may cache the compile_commands.json parse
internally keyed by db path, but a single prune() call must be independent
of any prior state — this keeps MCP per-call isolation guarantees intact.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class PruneResult:
    """Result of a single prune() invocation.

    Attributes:
        code: Pruned source code (or fully preprocessed code, for clang backend).
        skipped_ranges: List of (start_line, end_line) in the ORIGINAL file
                       that were dropped. Line numbers are 1-indexed inclusive.
                       Empty for clang backend (its output is the truth set).
        original_lines: Line count of the input file.
        pruned_lines: Non-empty line count of `code`.
        backend_name: Identifier of the backend that produced this result
                      ('regex' or 'clang').
        extra: Backend-specific metadata (e.g. compiler version, macros used).
    """
    code: str
    skipped_ranges: List[Tuple[int, int]] = field(default_factory=list)
    original_lines: int = 0
    pruned_lines: int = 0
    backend_name: str = "regex"
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def reduction_percentage(self) -> float:
        if self.original_lines <= 0:
            return 0.0
        return round(
            (self.original_lines - self.pruned_lines)
            / self.original_lines
            * 100,
            2,
        )


class PrunerBackend(ABC):
    """Abstract base for all pruner backends."""

    name: str = "abstract"

    @abstractmethod
    def prune(
        self,
        file_path: str,
        target: str,
        compile_db: str,
        mode: str = "physical",
    ) -> PruneResult:
        """Prune a single source file.

        Args:
            file_path: Absolute or relative path to a .c/.h/.cpp file.
            target: Product/macro name. Backend is responsible for combining
                    target with -D macros from compile_db.
            compile_db: Absolute path to compile_commands.json.
            mode: 'physical' (drop inactive lines) or 'virtual' (replace
                  with [INACTIVE] markers, preserve line numbers).

        Returns:
            PruneResult with code and metadata.
        """
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> Tuple[bool, str]:
        """Check whether this backend can run in the current environment.

        Returns:
            (ok, reason) — ok=True if available, reason='' on success or
            a human-readable reason on failure.
        """
        raise NotImplementedError


# Registry — populated by sibling modules importing + register_backend().
_REGISTRY: Dict[str, type] = {}


def register_backend(cls: type) -> type:
    """Decorator to register a PrunerBackend subclass in the global registry."""
    if not issubclass(cls, PrunerBackend):
        raise TypeError(f"{cls} must subclass PrunerBackend")
    _REGISTRY[cls.name] = cls
    return cls


def get_backend(name: str, **kwargs) -> PrunerBackend:
    """Instantiate a backend by name.

    Args:
        name: 'regex', 'clang', or 'auto'.
        **kwargs: Forwarded to backend __init__.

    Raises:
        ValueError: Unknown backend name.
        RuntimeError: Backend is registered but not usable in this env.
    """
    name = (name or "regex").lower()

    if name == "auto":
        # Try clang first (more accurate), fall back to regex.
        for candidate in ("clang", "regex"):
            cls = _REGISTRY.get(candidate)
            if cls is None:
                continue
            inst = cls(**kwargs)
            ok, _reason = inst.is_available()
            if ok:
                return inst
        # If neither registered, fall through to regex which always works.
        cls = _REGISTRY.get("regex")
        if cls is None:
            raise RuntimeError("No backends registered")
        return cls(**kwargs)

    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown backend: {name!r}. "
            f"Available: {sorted(_REGISTRY.keys())}"
        )
    inst = cls(**kwargs)
    ok, reason = inst.is_available()
    if not ok:
        raise RuntimeError(f"Backend {name!r} not available: {reason}")
    return inst


def list_backends() -> List[str]:
    """Return names of all registered backends."""
    return sorted(_REGISTRY.keys())
