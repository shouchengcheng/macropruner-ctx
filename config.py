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
    # Cross-compile SDK support for the clang backend (P4-1).
    "pruner.sysroot": "",               # path to a cross-compile SDK's sysroot
    "pruner.extra_target": "",          # e.g. 'riscv32-linux-musl'
    # Path safety: paths the pruner is allowed to read/write.
    # Empty list = no restriction (legacy behaviour). When set,
    # every file_path the LLM passes must resolve under one of
    # these roots, or the call is refused with [FATAL].
    "pruner.path_allowlist": [],
    # Path denylist: paths the pruner is NEVER allowed to touch,
    # even if they're under an allowlisted root. Useful for blocking
    # .git/, build/, node_modules/, etc.
    "pruner.path_denylist": [".git", "node_modules", ".venv"],
}


def _find_initproject_active_rc(project_root: Path) -> Optional[Path]:
    """Find the active project's .macroprunerrc under an init-project layout.

    init-project's spec (v2.4) places project-level assets in
    `ai/projects/<project_id>/.macroprunerrc`. To find it, we
    look for PROJECT_MANIFEST.md in `project_root` or any of its
    ancestors, parse out the active project, and check the
    expected path.

    Returns the path if it exists, else None. Does NOT call the
    manifest parser's full machinery — this is a fast path used
    on every config load.
    """
    # Walk up to find PROJECT_MANIFEST.md.
    cur = project_root.resolve()
    for _ in range(10):
        manifest = cur / "PROJECT_MANIFEST.md"
        if manifest.is_file():
            break
        if cur.parent == cur:
            return None
        cur = cur.parent
    else:
        return None

    # Quick parse: find `active_project:` line and `### <name>` blocks
    # with `project_id:` and `active: true`.
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    lines = text.splitlines()
    active_name = ""
    for ln in lines:
        if ln.startswith("active_project:"):
            active_name = ln.split(":", 1)[1].strip()
            break

    # Walk projects.
    current: Dict[str, str] = {}
    found_active = False
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            continue
        # Skip pure comment lines (but not ## / ### section headers).
        if stripped.startswith("#") and not stripped.startswith("##"):
            continue
        if ln.startswith("### "):
            if current and (current.get("name") == active_name or
                            current.get("active", "").lower() in
                            ("true", "yes", "1", "on")):
                if found_active:
                    break
                # Resolve the rc path.
                pid = current.get("project_id", "default")
                candidate = manifest.parent / "ai" / "projects" / pid / ".macroprunerrc"
                if candidate.is_file():
                    return candidate
                found_active = True
            current = {"name": ln[4:].strip()}
        elif ln.startswith("- ") and ":" in ln and current is not None:
            k, _, v = ln[2:].partition(":")
            current[k.strip()] = v.strip()

    # Trailing project (no ### terminator).
    if current and (current.get("name") == active_name or
                    current.get("active", "").lower() in ("true", "yes", "1", "on")):
        pid = current.get("project_id", "default")
        candidate = manifest.parent / "ai" / "projects" / pid / ".macroprunerrc"
        if candidate.is_file():
            return candidate

    return None


def _find_config(project_root: Optional[str] = None) -> Optional[Path]:
    """Locate the first existing .macroprunerrc.

    Search order:
      1. $MACROPRUNER_CONFIG env var (absolute path)
      2. <project_root>/.macroprunerrc  (project-level)
      3. <project_root>/ai/projects/<active_project>/.macroprunerrc
         (init-project project-level; P14-2)
      4. <project_root>/macroprunerrc  (without leading dot)
      5. <cwd>/.macroprunerrc
      6. <cwd>/ai/projects/<active_project>/.macroprunerrc
      7. <home>/.macroprunerrc

    The init-project lookup is a fast path — only consulted if
    no per-directory .macroprunerrc was found, and only against
    ancestors of project_root.
    """
    env = os.environ.get("MACROPRUNER_CONFIG")
    if env:
        p = Path(env)
        if p.is_file():
            return p
    roots = []
    if project_root:
        roots.append(Path(project_root))
    roots.append(Path.cwd())
    # Per-directory rc (the classic location)
    for root in roots:
        for name in (".macroprunerrc", "macroprunerrc"):
            candidate = root / name
            if candidate.is_file():
                return candidate
    # init-project project-level rc (P14-2). The active project
    # is read from PROJECT_MANIFEST.md; the rc is at
    # ai/projects/<project_id>/.macroprunerrc relative to the
    # manifest's parent (which we treat as repo_root).
    for root in roots:
        rc = _find_initproject_active_rc(root)
        if rc is not None:
            return rc
    # Last resort: home directory.
    for name in (".macroprunerrc", "macroprunerrc"):
        candidate = Path.home() / name
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
