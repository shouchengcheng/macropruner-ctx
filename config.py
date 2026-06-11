"""Project configuration loader for MacroPruner-Ctx.

Reads `.macroprunerrc` from the project root (or `~/.macroprunerrc` as
a fallback for global defaults). Format is intentionally minimal — a
TOML-ish KEY = VALUE syntax with [sections] — so we don't pull in
`tomli` / `tomllib` as a runtime dependency.

Why no real TOML?
  - The Python 3.10 venv in our target environments doesn't ship
    `tomllib` (that's 3.11+). Adding `tomli` for one config file is
    overkill.
  - Our config surface is ~6 keys. A 50-line parser handles it
    deterministically and parses faster than any full TOML library.

Search order (first hit wins):
  1. $MACROPRUNER_CONFIG env var (absolute path)
  2. <project_root>/.macroprunerrc
  3. <project_root>/macroprunerrc
  4. ~/.macroprunerrc

If none of these exist, all keys fall back to defaults defined here.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional


# Default values, used when a key is missing from .macroprunerrc.
# Nested keys use dot notation: "pruner.target", "deps.max_depth",
# etc. Sections in the config file map to dot prefixes.
DEFAULTS: Dict[str, Any] = {
    "pruner.default_target": "",
    "pruner.compile_db": "",
    "pruner.default_backend": "regex",  # 'regex' | 'clang' | 'auto'
    "pruner.default_mode": "physical",  # 'physical' | 'virtual'
    "pruner.default_max_depth": 2,
    "pruner.token_budget": 0,           # 0 = no cap (Stage 4 placeholder)
    "pruner.include_dirs": [],          # extra -I for headers
}


def _find_config(project_root: Optional[str] = None) -> Optional[Path]:
    """Locate the first existing .macroprunerrc."""
    env = os.environ.get("MACROPRUNER_CONFIG")
    if env:
        p = Path(env)
        if p.is_file():
            return p
    roots = []
    if project_root:
        roots.append(Path(project_root))
    roots.append(Path.cwd())
    roots.append(Path.home())
    for root in roots:
        for name in (".macroprunerrc", "macroprunerrc"):
            candidate = root / name
            if candidate.is_file():
                return candidate
    return None


def _coerce(value: str) -> Any:
    """Coerce a string value to its most likely type.

    - 'true' / 'false' -> bool
    - '[' / ']' bracketed lists -> list of strings
    - integers and floats parsed as numbers
    - everything else stays a string (with .strip())
    """
    value = value.strip()
    if value.lower() in ("true", "yes", "on"):
        return True
    if value.lower() in ("false", "no", "off"):
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_coerce(p) for p in _split_list(inner)]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    # Strip matching quotes.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _split_list(s: str):
    """Naive comma split, ignoring commas inside brackets/quotes.

    Good enough for our use case (include_dirs is a flat list of paths).
    """
    out = []
    buf = []
    depth = 0
    quote = None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            continue
        if ch in ("[", "("):
            depth += 1
            buf.append(ch)
            continue
        if ch in ("]", ")"):
            depth -= 1
            buf.append(ch)
            continue
        if ch == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return out


def load(project_root: Optional[str] = None) -> Dict[str, Any]:
    """Load the .macroprunerrc configuration merged with DEFAULTS.

    Returns a dict with all keys from DEFAULTS populated. Unknown keys
    are kept in an '_extra' key for forward-compat (not consumed by
    the core tool, but available for extensions).
    """
    config = dict(DEFAULTS)
    config["_extra"] = {}

    path = _find_config(project_root)
    if path is None:
        config["_config_path"] = ""
        return config
    config["_config_path"] = str(path)

    section = ""  # current [section]; keys are flat-keyed in this version
    # When no section is active, bare keys implicitly belong to [pruner].
    # This matches the common case where users want to write
    # "default_target = X" without a section header.
    default_section = "pruner"
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.split("#", 1)[0].rstrip()  # strip comments + trailing
                if not line.strip():
                    continue
                if line.lstrip().startswith("[") and line.rstrip().endswith("]"):
                    section = line.strip()[1:-1].strip()
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = _coerce(value)
                if section:
                    full_key = f"{section}.{key}"
                else:
                    full_key = f"{default_section}.{key}"
                if full_key in DEFAULTS:
                    config[full_key] = value
                else:
                    config["_extra"][full_key] = value
    except (OSError, UnicodeDecodeError) as e:
        # Don't crash the tool over a broken config — fall back to defaults
        # and tag the path so callers can debug.
        config["_config_error"] = str(e)
    return config


def resolve_compile_db(config: Dict[str, Any], project_root: Optional[str] = None) -> Optional[str]:
    """Resolve the compile_db path from config.

    Returns absolute path if found, else None. Resolution order:
      1. config['pruner.compile_db'] (relative paths are resolved
         against project_root, never against cwd — project_root must
         be passed explicitly to avoid CWD surprises in CI)
      2. common build-output locations: build/compile_commands.json,
         compile_commands.json
    """
    candidate = config.get("pruner.compile_db", "")
    if candidate:
        if not project_root:
            return None
        p = Path(candidate)
        if not p.is_absolute():
            p = Path(project_root) / p
        if p.is_file():
            return str(p.resolve())
        return None
    if not project_root:
        return None
    # Fall back to common locations.
    for rel in ("build/compile_commands.json", "compile_commands.json"):
        p = Path(project_root) / rel
        if p.is_file():
            return str(p.resolve())
    return None
