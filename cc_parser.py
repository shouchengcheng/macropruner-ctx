"""
Compile DB Parser - Extracts per-file active macros from compile_commands.json.
Handles space/escape-safe command tokenization and -D flag extraction.
"""

import json
import shlex
import re
from pathlib import Path
from typing import Dict, List, Optional


class CompileDBParser:
    """Parses compile_commands.json to extract per-source-file active macro definitions."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path).resolve()
        self._entries: Optional[List[Dict]] = None

    def _load(self) -> List[Dict]:
        if self._entries is not None:
            return self._entries
        with open(self.db_path, "r") as f:
            self._entries = json.load(f)
        return self._entries

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


def extract_macros_for_file(
    compile_db_path: str, source_file: str
) -> Dict[str, Optional[str]]:
    """Convenience function: extract macros for a single source file."""
    parser = CompileDBParser(compile_db_path)
    return parser.extract_macros(source_file)
