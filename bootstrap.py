"""Auto-generate .macroprunerrc by scanning the project.

Designed to be called by the `bootstrap_config` MCP tool. Has two
modes:

  - dry_run (default):  scan the project, return a recommended
                       .macroprunerrc as text. No files written.

  - apply:              scan the project, write the recommended
                       .macroprunerrc to disk. Refuses to overwrite
                       an existing file unless force=True.

Scanning strategy, in priority order:

  1. PROJECT_MANIFEST.md (init-project skill artifact)
     - The manifest declares `active_project` and a Project Matrix
       with `compile_commands` paths per project. We use these
       directly — no heuristic guessing.

  2. compile_commands.json in standard locations
     - `<project>/ai/projects/<project_id>/compile_commands/*.json`
       (init-project standard)
     - `<project>/build/compile_commands.json`
     - `<project>/compile_commands.json`
     - Full-project glob

  3. Heuristic target inference
     - If we found a cdb: parse it, find the most common -D flag
       set, name the target after a PRODUCT_* / CHIP_* / BUILD_TYPE_*
       macro whose value differs across the entries.
     - If we have no cdb: target = "DEFAULT" (will trigger
       [FATAL] for any actual read_c call).

Path conventions (init-project 2.4 spec):
  - .macroprunerrc lives in `ai/projects/<project_id>/.macroprunerrc`
    (project-level, isolated from other projects in the same repo)
  - The path_allowlist in the generated rc is set to
    `<project_root>/` so P12-1 safety defaults to "on" for the
    whole project.

This module deliberately knows nothing about MCP — it returns
plain Python data. mcp_server.py wraps it in a tool.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional, Dict, List, Tuple


# Map cdb macro names to user-friendly target prefixes. When
# the cdb defines -DPRODUCT_TYPE=3, we want target=PRODUCT_3
# (matches what the user wrote in #ifdef PRODUCT_TYPE == 3).
_CANONICAL_NAME = {
    "PRODUCT_TYPE": "PRODUCT",
    "CHIP_TYPE": "CHIP",
    "BOARD_TYPE": "BOARD",
}


# Fields we always emit in the generated .macroprunerrc, in order.
# Comments on the right are written verbatim to the file.
DEFAULT_RC_FIELDS: List[Tuple[str, str, object]] = [
    # (key, comment, value)
    ("pruner.default_target",
     "Target product/macro name (e.g. PRODUCT_3, ws63).",
     ""),
    ("pruner.compile_db",
     "Path to compile_commands.json (relative to project root).",
     ""),
    ("pruner.default_backend",
     "Backend: 'regex' (default), 'clang' (oracle), or 'auto'.",
     "auto"),
    ("pruner.default_mode",
     "Pruning mode: 'physical' or 'virtual'.",
     "physical"),
    ("pruner.default_max_depth",
     "include tree traversal depth (1-5) for read_c_with_deps.",
     2),
    ("pruner.token_budget",
     "Per-call token cap. 0 = unlimited. (Stage 4)",
     0),
    ("pruner.path_allowlist",
     "Paths the pruner is allowed to read/write. Empty = no restriction.",
     []),
    ("pruner.path_denylist",
     "Paths always blocked (subtree match).",
     [".git", "node_modules", ".venv"]),
]


def _find_manifest(start: Path) -> Optional[Path]:
    """Walk up from `start` looking for PROJECT_MANIFEST.md.

    init-project's contract: the manifest lives at the repo root.
    We allow up to 10 levels of upward search.
    """
    cur = start.resolve()
    for _ in range(10):
        candidate = cur / "PROJECT_MANIFEST.md"
        if candidate.is_file():
            return candidate
        if cur.parent == cur:
            return None  # hit filesystem root
        cur = cur.parent
    return None


def _parse_manifest(path: Path) -> Optional[Dict]:
    """Parse PROJECT_MANIFEST.md into a structured dict.

    The format is human-readable Markdown with `key: value` lines
    inside `## Section` headers. We do a permissive parse:

      - `## Project Matrix` introduces a per-project block
      - Each `### Project Name` header starts a new project
      - `- key: value` lines under a project are that project's fields
      - Top-level (before any `##`) `key: value` lines are the
        repo's overall fields (`repo_root`, `active_project`, ...)

    Returns:
      {
        "repo_root": str,
        "active_project": str,
        "projects": [
            {"name": ..., "active": bool, "compile_commands": ..., ...},
            ...
        ]
      }
      or None if the file doesn't look like a manifest.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    top: Dict[str, str] = {}
    projects: List[Dict[str, str]] = []
    current_project: Optional[Dict[str, str]] = None

    in_matrix = False
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        # Skip pure comment lines. Note: `## Section` and
        # `### Project` are valid headers, NOT comments; only
        # `# foo` (single hash) is a comment.
        if line.startswith("#") and not line.startswith("##"):
            continue
        if line.startswith("## "):
            in_matrix = line.startswith("## Project Matrix")
            if current_project is not None:
                projects.append(current_project)
                current_project = None
            continue
        if line.startswith("### ") and in_matrix:
            if current_project is not None:
                projects.append(current_project)
            # The project name is the rest of the header.
            current_project = {"name": line[4:].strip()}
            continue
        if ":" not in line:
            continue
        if line.startswith("- "):
            # Project-level "- key: value"
            key, _, value = line[2:].partition(":")
            key = key.strip()
            value = value.strip()
            if current_project is not None:
                current_project[key] = value
        elif current_project is None and not in_matrix:
            # Top-level "key: value" (no dash). These are the
            # repo's overall fields, only valid before the matrix
            # section starts.
            key, _, value = line.partition(":")
            top[key.strip()] = value.strip()

    if current_project is not None:
        projects.append(current_project)

    if "active_project" not in top and not any(
        p.get("active", "").lower() in ("true", "yes", "1") for p in projects
    ):
        return None  # not a valid manifest

    return {
        "repo_root": top.get("repo_root", str(path.parent)),
        "active_project": top.get("active_project", ""),
        "projects": projects,
    }


def _select_active_project(manifest: Dict) -> Optional[Dict]:
    """Pick the active project from a parsed manifest.

    Priority:
      1. The project whose name matches `active_project` at the top.
      2. The first project with `active: true`.
      3. None.
    """
    target_name = manifest.get("active_project", "").strip()
    for p in manifest["projects"]:
        if p.get("name") == target_name:
            return p
    for p in manifest["projects"]:
        if p.get("active", "").lower() in ("true", "yes", "1", "on"):
            return p
    return None


def _resolve_cdb_in_manifest(project: Dict, repo_root: str) -> Optional[str]:
    """Resolve the compile_commands path declared in a manifest project.

    Returns the absolute path if the file exists, else None.
    """
    raw = project.get("compile_commands", "").strip()
    if not raw:
        return None
    # The manifest stores paths relative to repo_root. Absolute
    # paths are also accepted.
    if os.path.isabs(raw):
        candidate = Path(raw)
    else:
        candidate = Path(repo_root) / raw
    if candidate.is_file():
        return str(candidate.resolve())
    return None


def _find_cdb_heuristic(start: Path) -> Optional[str]:
    """Look for compile_commands.json in standard init-project locations.

    Order:
      1. <start>/ai/projects/*/compile_commands/compile_commands*.json
      2. <start>/build/compile_commands.json
      3. <start>/compile_commands.json
      4. recursive search of <start> (depth-limited)
    """
    # 1. init-project standard: per-project compile_commands dir
    ai_projects = start / "ai" / "projects"
    if ai_projects.is_dir():
        for proj_dir in ai_projects.iterdir():
            if not proj_dir.is_dir():
                continue
            cdb_dir = proj_dir / "compile_commands"
            if cdb_dir.is_dir():
                cands = sorted(cdb_dir.glob("compile_commands*.json"))
                if cands:
                    return str(cands[0].resolve())

    # 2. <start>/build/compile_commands.json
    cand = start / "build" / "compile_commands.json"
    if cand.is_file():
        return str(cand.resolve())

    # 3. <start>/compile_commands.json
    cand = start / "compile_commands.json"
    if cand.is_file():
        return str(cand.resolve())

    # 4. shallow recursive search (max depth 4, skip heavy dirs)
    SKIP = {".git", "node_modules", ".venv", "build", "dist", "__pycache__"}
    queue: List[Tuple[Path, int]] = [(start, 0)]
    while queue:
        p, depth = queue.pop(0)
        if depth > 4:
            continue
        try:
            for child in p.iterdir():
                if child.is_dir():
                    if child.name in SKIP:
                        continue
                    queue.append((child, depth + 1))
                elif child.name == "compile_commands.json" or (
                    child.name.startswith("compile_commands") and
                    child.suffix == ".json"
                ):
                    return str(child.resolve())
        except (PermissionError, OSError):
            continue

    return None


# Heuristic target names: which -D macros are "naming" macros
# (i.e. distinguish one product variant from another). The first
# match wins; the rest are ignored.
#
# Examples of what these get converted to (target = f"{name}_{value}"):
#   -DPRODUCT_TYPE=3  ->  PRODUCT_3    (PRODUCT_ wins over PRODUCT_TYPE_)
#   -DPRODUCT=5       ->  PRODUCT_5
#   -DCHIP=WS63       ->  CHIP_WS63
# When both PRODUCT and PRODUCT_TYPE are defined in the cdb,
# PRODUCT wins (it's listed first). When only PRODUCT_TYPE is
# defined, the canonicalization step below maps it to PRODUCT
# so the resulting target name is "PRODUCT_3" rather than
# "PRODUCT_TYPE_3" (the more user-friendly form).
TARGET_NAMING_MACROS = (
    "PRODUCT",
    "PRODUCT_NAME",
    "PRODUCT_TYPE",
    "CHIP",
    "CHIP_TYPE",
    "CHIP_NAME",
    "BUILD_TYPE",
    "BUILD_VARIANT",
    "BUILD",
    "TARGET",
    "TARGET_NAME",
    "VARIANT",
    "BOARD",
    "BOARD_TYPE",
)

def _infer_target_from_cdb(cdb_path: str) -> str:
    """Infer a sensible target name from a compile_commands.json.

    Strategy:
      1. Parse the cdb, group entries by their -D flag set.
      2. For each unique set, look for a "naming" macro
         (PRODUCT_TYPE, CHIP, etc.).
      3. Pick the most common naming value across all entries.
      4. If no naming macro is found, fall back to "DEFAULT".

    Examples:
      cdb has 50 entries with -DPRODUCT_TYPE=3 and 30 with -DPRODUCT_TYPE=5
        → returns "PRODUCT_3" (most common)
      cdb has only -DCHIP=WS63
        → returns "CHIP_WS63"
      cdb has -DPRODUCT_TYPE=3 -DCHIP=WS63
        → returns "PRODUCT_3" (PRODUCT_ takes priority)
      cdb has no recognizable naming macro
        → returns "DEFAULT"
    """
    try:
        with open(cdb_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return "DEFAULT"

    if not isinstance(data, list):
        return "DEFAULT"

    # value_counts: target_name -> entry count
    value_counts: Dict[str, int] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        command = entry.get("command", "") or entry.get("arguments", "")
        if isinstance(command, list):
            command = " ".join(command)
        # Extract -D macros from the command. Match `-DNAME=value`
        # or `-DNAME` (treating -DNAME as -DNAME=1).
        names = re.findall(r"-D([A-Za-z_][A-Za-z0-9_]*)(?:=([^\s]+))?", command)
        names = [(n, v if v else "1") for n, v in names]
        # Pick the highest-priority naming macro that's present.
        chosen = None
        for naming in TARGET_NAMING_MACROS:
            for n, v in names:
                if n == naming:
                    chosen = (n, v)
                    break
            if chosen:
                break
        if chosen is None:
            target = "DEFAULT"
        else:
            n, v = chosen
            # Strip quotes from the value if present.
            v = v.strip("'\"")
            # Canonicalize the macro name. e.g. -DPRODUCT_TYPE=3 should
            # yield target="PRODUCT_3", not "PRODUCT_TYPE_3". This
            # matches the typical user mental model: "what product
            # variant is this?" not "what is the type field called?"
            n = _CANONICAL_NAME.get(n, n)
            target = f"{n}_{v}"
        value_counts[target] = value_counts.get(target, 0) + 1

    if not value_counts:
        return "DEFAULT"

    # Return the most common target name.
    return max(value_counts.items(), key=lambda kv: kv[1])[0]


def _format_rc(recommended: Dict[str, object], comments: Dict[str, str]) -> str:
    """Render a recommended config dict as a .macroprunerrc file.

    The format is KEY = VALUE with section headers derived from
    dotted keys (e.g. `pruner.default_target` lives under `[pruner]`).
    """
    by_section: Dict[str, List[Tuple[str, object]]] = {}
    for key, value in recommended.items():
        if "." in key:
            section, _, sub = key.partition(".")
            by_section.setdefault(section, []).append((sub, value))
        else:
            by_section.setdefault("pruner", []).append((key, value))

    lines: List[str] = []
    lines.append("# .macroprunerrc — auto-generated by macropruner-ctx bootstrap_config")
    lines.append("# Review the values below and commit the file to your repo.")
    lines.append("")

    for section in sorted(by_section.keys()):
        lines.append(f"[{section}]")
        for k, v in by_section[section]:
            comment = comments.get(f"{section}.{k}", "")
            if comment:
                lines.append(f"# {comment}")
            if isinstance(v, list):
                rendered = "[" + ", ".join(_format_value(x) for x in v) + "]"
            else:
                rendered = _format_value(v)
            lines.append(f"{k} = {rendered}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _format_value(v: object) -> str:
    """Render a single config value."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        # Quote strings that contain whitespace or special chars.
        if v == "" or any(c in v for c in " \t[]#=\"'"):
            return json.dumps(v)
        return v
    return str(v)


def scan(
    project_root: Optional[str] = None,
) -> Dict:
    """Scan the project and produce a recommended config.

    Returns a dict with:
      - "source": "init-project manifest" | "heuristic" | "none"
      - "manifest_path": absolute path or None
      - "active_project": project dict from manifest or None
      - "compile_db": absolute path or None
      - "target": inferred target name (always a string)
      - "recommended": flat key->value dict suitable for the rc file
      - "rc_path": absolute path the tool would write to
      - "rc_already_exists": bool

    The function does NOT write anything — call apply() for that.
    """
    root = Path(project_root) if project_root else Path(os.getcwd())

    source = "none"
    manifest_path = _find_manifest(root)
    active_project = None
    compile_db: Optional[str] = None
    target = "DEFAULT"
    rc_subdir = "ai/projects/default"  # used if no manifest

    if manifest_path is not None:
        manifest = _parse_manifest(manifest_path)
        if manifest is not None:
            source = "init-project manifest"
            active_project = _select_active_project(manifest)
            if active_project is not None:
                repo_root = manifest.get("repo_root") or str(manifest_path.parent)
                compile_db = _resolve_cdb_in_manifest(active_project, repo_root)
                project_id = active_project.get("project_id", "default")
                rc_subdir = f"ai/projects/{project_id}"
            else:
                # Manifest found but no active project — fall through to
                # heuristic. Better something than nothing.
                source = "init-project manifest (no active project)"

    if compile_db is None:
        compile_db = _find_cdb_heuristic(root)

    if compile_db is not None:
        target = _infer_target_from_cdb(compile_db)

    # Build the recommended config.
    repo_root = (
        str(manifest_path.parent) if manifest_path
        else str(root.resolve())
    )
    # If we have a manifest, the repo_root might be different from
    # the cwd. Re-resolve cdb relative to repo_root for the rc value.
    rc_compile_db = ""
    if compile_db:
        try:
            rc_compile_db = str(Path(compile_db).relative_to(repo_root))
        except ValueError:
            # cdb is outside repo_root; store absolute path.
            rc_compile_db = compile_db

    recommended: Dict[str, object] = {
        "pruner.default_target": target,
        "pruner.compile_db": rc_compile_db,
        "pruner.default_backend": "auto",
        "pruner.default_mode": "physical",
        "pruner.default_max_depth": 2,
        "pruner.token_budget": 0,
        # path_allowlist defaults to the project root so P12-1
        # safety is "on" out of the box.
        "pruner.path_allowlist": [repo_root],
        "pruner.path_denylist": [".git", "node_modules", ".venv"],
    }

    rc_path = os.path.join(repo_root, rc_subdir, ".macroprunerrc")
    rc_already_exists = os.path.isfile(rc_path)

    return {
        "source": source,
        "manifest_path": str(manifest_path) if manifest_path else None,
        "active_project": active_project,
        "compile_db": compile_db,
        "target": target,
        "recommended": recommended,
        "rc_path": rc_path,
        "rc_already_exists": rc_already_exists,
    }


def apply(
    project_root: Optional[str] = None,
    force: bool = False,
) -> Dict:
    """Scan + write the recommended .macroprunerrc.

    Returns a dict (same shape as scan() plus "written" bool).
    Refuses to overwrite an existing file unless force=True.
    The parent directory of the target path is created if missing.
    """
    result = scan(project_root=project_root)

    if result["rc_already_exists"] and not force:
        result["written"] = False
        result["refused_reason"] = (
            f"{result['rc_path']} already exists; pass force=True to overwrite"
        )
        return result

    rc_path = result["rc_path"]
    rc_dir = os.path.dirname(rc_path)
    os.makedirs(rc_dir, exist_ok=True)

    comments = {
        "pruner.default_target": "Target product/macro (e.g. PRODUCT_3, ws63).",
        "pruner.compile_db": "Path to compile_commands.json (relative to project root).",
        "pruner.default_backend": "Backend: 'regex' (default), 'clang' (oracle), or 'auto'.",
        "pruner.default_mode": "Pruning mode: 'physical' or 'virtual'.",
        "pruner.default_max_depth": "include tree depth (1-5) for read_c_with_deps.",
        "pruner.token_budget": "Per-call token cap. 0 = unlimited.",
        "pruner.path_allowlist": "Paths the pruner may read/write. Empty = no restriction.",
        "pruner.path_denylist": "Paths always blocked (subtree match).",
    }
    rendered = _format_rc(result["recommended"], comments)

    with open(rc_path, "w", encoding="utf-8") as f:
        f.write(rendered)

    result["written"] = True
    result["rc_content"] = rendered
    return result
