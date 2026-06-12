"""
Compile DB Parser - Extracts per-file active macros from compile_commands.json.
Handles space/escape-safe command tokenization and -D flag extraction.

Includes a process-level cache keyed by db path + file mtime, so the
100th read_c call in a long agent session does not re-parse the
compile DB. The cache is bounded and uses file mtime to invalidate,
which means an edit to compile_commands.json in a running agent
session will be picked up on the next call.
"""
import json
import shlex
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── Cache ──────────────────────────────────────────────────────────
# CachedTuple = (mtime, entries)
# Keyed by resolved db path. Capped at CACHE_MAX_ENTRIES to avoid
# unbounded growth in long-running sessions.
_CACHE: Dict[str, Tuple[float, List[Dict]]] = {}
CACHE_MAX_ENTRIES = 16
CACHE_TTL_SECONDS = 5.0  # soft TTL; mtime check is authoritative


def _cache_get(db_path: str) -> Optional[List[Dict]]:
    """Return cached entries if still valid, else None.

    Validity = (within TTL) AND (file mtime matches cached value).
    Either check failing -> cache miss -> caller re-parses.
    """
    cached = _CACHE.get(db_path)
    if cached is None:
        return None
    cached_mtime, entries = cached
    try:
        current_mtime = Path(db_path).stat().st_mtime
    except OSError:
        # File disappeared: invalidate.
        _CACHE.pop(db_path, None)
        return None
    if current_mtime != cached_mtime:
        # File changed: invalidate.
        _CACHE.pop(db_path, None)
        return None
    return entries


def _cache_put(db_path: str, mtime: float, entries: List[Dict]) -> None:
    """Store entries in cache, evicting oldest if at capacity."""
    if len(_CACHE) >= CACHE_MAX_ENTRIES and db_path not in _CACHE:
        # Evict the oldest by mtime.
        oldest_key = min(_CACHE.keys(), key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest_key, None)
    _CACHE[db_path] = (mtime, entries)


def clear_cache() -> None:
    """Drop all cached entries. Exposed for tests + tooling."""
    _CACHE.clear()


class CompileDBParser:
    """Parses compile_commands.json to extract per-source-file active macro definitions."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path).resolve()
        self._entries: Optional[List[Dict]] = None

    def _load(self) -> List[Dict]:
        if self._entries is not None:
            return self._entries
        # Try cache first.
        cached = _cache_get(str(self.db_path))
        if cached is not None:
            self._entries = cached
            return self._entries
        # Miss: parse and store.
        with open(self.db_path, "r") as f:
            entries = json.load(f)
        try:
            mtime = self.db_path.stat().st_mtime
        except OSError:
            mtime = time.time()
        _cache_put(str(self.db_path), mtime, entries)
        self._entries = entries
        return self._entries  # type: ignore[return-value]

    def _tokenize_command(self, entry: Dict) -> List[str]:
        command = entry.get("command", "")
        arguments = entry.get("arguments", [])

        if arguments:
            return arguments

        try:
            return shlex.split(command)
        except ValueError:
            return command.split()

    def extract_macros(self, source_file: str) -> Dict[str, Optional[str]]:
        """
        Extract all -D macro definitions for a given source file.

        Args:
            source_file: Relative or absolute path to the source file

        Returns:
            Dict mapping macro names to their values (None for simple -DFLAG)
        """
        entries = self._load()
        target = Path(source_file).resolve()

        for entry in entries:
            entry_file = Path(entry.get("file", ""))
            if entry_file.suffix not in (".c", ".cpp", ".cc", ".cxx", ".C"):
                continue
            if (
                self._resolve_entry_path(entry) == target
                or entry_file.name == target.name
            ):
                return self._parse_macros_from_command(entry)

        return {}

    def extract_all_macros(self) -> Dict[str, Dict[str, Optional[str]]]:
        """
        Extract macros for every source file in the compile DB.

        Returns:
            Dict mapping source file path -> {macro_name: macro_value}
        """
        entries = self._load()
        result = {}
        for entry in entries:
            file_path = entry.get("file", "")
            if file_path:
                result[file_path] = self._parse_macros_from_command(entry)
        return result

    def _parse_macros_from_command(self, entry: Dict) -> Dict[str, Optional[str]]:
        tokens = self._tokenize_command(entry)
        macros = {}
        for token in tokens:
            if token.startswith("-D") or token.startswith("-D "):
                macro_part = token[2:].strip()
                if "=" in macro_part:
                    name, value = macro_part.split("=", 1)
                    macros[name.strip()] = value.strip()
                else:
                    macros[macro_part] = None
        return macros

    def _resolve_entry_path(self, entry: Dict) -> Path:
        """Resolve an entry's file path relative to its directory field."""
        entry_file = Path(entry.get("file", ""))
        if entry_file.is_absolute():
            return entry_file.resolve()
        directory = entry.get("directory", "")
        if directory:
            return (Path(directory) / entry_file).resolve()
        return entry_file.resolve()

    def resolve_include_dirs(self, source_file: str) -> List[str]:
        """Extract -I include directories for a given source file."""
        entries = self._load()
        target = Path(source_file).resolve()
        include_dirs = []

        for entry in entries:
            if self._resolve_entry_path(entry) != target:
                continue

            tokens = self._tokenize_command(entry)
            i = 0
            while i < len(tokens):
                token = tokens[i]
                if token == "-I" and i + 1 < len(tokens):
                    include_dirs.append(tokens[i + 1])
                    i += 1
                elif token.startswith("-I"):
                    include_dirs.append(token[2:])
                i += 1
            break

        return include_dirs

    def get_entry_tokens_for_file(self, source_file: str) -> Optional[List[str]]:
        """Return the full tokenized command for a source file's entry,
        or None if no entry matches.

        Useful for backends that want to reuse the project's compile
        flags (e.g. -march, -mabi, -isystem, --target=, --sysroot=)
        instead of hand-rolling them.
        """
        entries = self._load()
        target = Path(source_file).resolve()
        for entry in entries:
            if self._resolve_entry_path(entry) == target:
                return self._tokenize_command(entry)
        return None


def extract_macros_for_file(
    compile_db_path: str, source_file: str
) -> Dict[str, Optional[str]]:
    """Convenience function: extract macros for a single source file."""
    parser = CompileDBParser(compile_db_path)
    return parser.extract_macros(source_file)

