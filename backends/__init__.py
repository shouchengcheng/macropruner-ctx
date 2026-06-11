"""Backends subpackage — Pluggable pruner backends.

A backend takes a C/C++ source file plus a target/macro context and
returns a PruneResult (pruned code + skipped-line ranges + metadata).

Two backends ship in-tree:

  - regex (default, pure-Python, fast)  →  backends.regex_backend.RegexBackend
  - clang (optional, ground-truth)      →  backends.clang_backend.ClangBackend

Both are auto-registered when the subpackage is imported. `get_backend`
and `list_backends` are the public entry points.
"""
from .base import (
    PruneResult,
    PrunerBackend,
    get_backend,
    list_backends,
    register_backend,
)

# Side-effect imports — register concrete backends into the registry.
from . import regex_backend  # noqa: F401
from . import clang_backend  # noqa: F401

__all__ = [
    "PruneResult",
    "PrunerBackend",
    "get_backend",
    "list_backends",
    "register_backend",
]
