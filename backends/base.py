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
from typing import Dict, List, Optional, Tuple, Union


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
        original_code: The full original source. Set by backends after
                      computing token estimates. None if not tracked.
        extra: Backend-specific metadata (e.g. compiler version, macros used).
    """
    code: str
    skipped_ranges: List[Tuple[int, int]] = field(default_factory=list)
    original_lines: int = 0
    pruned_lines: int = 0
    backend_name: str = "regex"
    original_code: Optional[str] = None
    extra: Dict[str, str] = field(default_factory=dict)
    # What target/compile_db the backend actually used (post-fallback).
    # MCP tools render this in the banner so the user sees the real
    # values, not the empty defaults that were passed in.
    effective_target: str = ""
    effective_compile_db: str = ""

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

    @property
    def token_estimate(self) -> Dict[str, Union[int, float]]:
        """LLM token estimates for the before/after pair.

        Uses char_estimate (chars / 3.7) as the authoritative number
        since it correlates best with what LLMs actually pay for. For
        full numbers, see token_counter.estimate_pair.

        Returns a dict with: original_tokens, pruned_tokens,
        saved_tokens, saved_pct. Returns zeros if original_code
        wasn't tracked.
        """
        # Local import to avoid a hard dep at module import time.
        from token_counter import char_estimate

        if self.original_code is None:
            # Fall back to char counts derived from the code we have.
            return {
                "original_tokens": char_estimate(self.code) + char_estimate(
                    "\n".join("x" for _ in range(self.original_lines - self.pruned_lines))
                ),
                "pruned_tokens": char_estimate(self.code),
                "saved_tokens": 0,
                "saved_pct": 0.0,
            }
        o = char_estimate(self.original_code)
        p = char_estimate(self.code)
        saved = max(0, o - p)
        return {
            "original_tokens": o,
            "pruned_tokens": p,
            "saved_tokens": saved,
            "saved_pct": round(saved / o * 100, 2) if o > 0 else 0.0,
        }


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
        **kwargs: Forwarded to backend __init__ (e.g. `sysroot=` for
                  the clang backend on cross-compile SDKs).

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
            try:
                inst = cls(**kwargs)
            except TypeError:
                # Backend doesn't accept these kwargs — skip it.
                continue
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
    try:
        inst = cls(**kwargs)
    except TypeError as e:
        # Specific backend doesn't accept these kwargs; retry without them.
        if kwargs:
            inst = cls()
        else:
            raise RuntimeError(f"Backend {name!r} init failed: {e}")
    ok, reason = inst.is_available()
    if not ok:
        raise RuntimeError(f"Backend {name!r} not available: {reason}")
    return inst


def list_backends() -> List[str]:
    """Return names of all registered backends."""
    return sorted(_REGISTRY.keys())
