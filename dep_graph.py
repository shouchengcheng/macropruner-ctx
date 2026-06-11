"""
C/C++ Dependency Graph Builder - Parses #include directives and builds
a dependency graph. Two traversal modes:

  - `build()`: unconditional — follow every #include up to max_depth.
  - `conditional_build()`: use an active-macro dict to evaluate
    surrounding #if/#ifdef blocks. Only follow an #include if every
    #if block it sits inside is active.

Stage 3 Phase 2 lives here. The conditional parser is the same
expression evaluator used by the pruner (ExpressionEvaluator from
expr_eval), so #if PRODUCT == 3, defined(A) && defined(B), and
IS_ENABLED(X) all work.

The `active_includes` set returned alongside the graph is consumed by
the MCP server's `read_c_with_deps` to know which dependency files to
fetch in the conditional-aware mode.
"""
import os
import re
from typing import Dict, List, Optional, Set, Tuple


# Match preprocessor directives. Groups:
#   1: directive name (ifdef/ifndef/if/elif/else/endif/include)
#   2: rest of line (condition, or #include argument with leading whitespace)
_DIRECTIVE_RE = re.compile(
    r"^\s*#\s*(ifdef|ifndef|if|elif|else|endif|include)\b(.*)$"
)
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')


class DependencyGraph:
    INCLUDE_PATTERN = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')

    def __init__(self):
        self.graph: Dict[str, List[str]] = {}
        self.file_map: Dict[str, str] = {}
        self.resolved_paths: Dict[str, str] = {}

    # ── Unconditional build (Stage 3 Phase 1, unchanged) ───────────

    def build(
        self,
        root_file: str,
        include_dirs: Optional[List[str]] = None,
        max_depth: int = 10,
    ) -> Dict[str, List[str]]:
        """Walk #include tree unconditionally. Existing API."""
        self.graph = {}
        self.file_map = {}
        self.resolved_paths = {}
        include_dirs = include_dirs or []
        resolved = self._resolve_path(root_file, include_dirs)
        if not resolved:
            return self.graph
        self._traverse(resolved, include_dirs, set(), max_depth, 0)
        return self.graph

    def _traverse(
        self,
        file_path: str,
        include_dirs: List[str],
        visited: Set[str],
        max_depth: int,
        depth: int,
    ):
        if depth >= max_depth:
            return
        if file_path in visited:
            return
        visited.add(file_path)

        basename = os.path.basename(file_path)
        self.graph[basename] = []
        self.resolved_paths[basename] = file_path

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except (IOError, OSError):
            return

        for line in content.splitlines():
            m = self.INCLUDE_PATTERN.match(line)
            if not m:
                continue
            header_name = m.group(1)
            self.graph[basename].append(header_name)

            header_path = self._resolve_path(header_name, include_dirs, file_path)
            if header_path and header_path not in visited:
                self._traverse(header_path, include_dirs, visited, max_depth, depth + 1)

    # ── Conditional build (Stage 3 Phase 2) ────────────────────────

    def conditional_build(
        self,
        root_file: str,
        include_dirs: Optional[List[str]] = None,
        max_depth: int = 10,
        active_macros: Optional[Dict[str, Optional[str]]] = None,
    ) -> Tuple[Dict[str, List[str]], Set[str]]:
        """Walk #include tree respecting #if guards.

        For each #include we encounter, we evaluate the chain of #if/
        #ifdef/#ifndef blocks surrounding it. We follow the include
        ONLY if every enclosing block is active.

        Returns:
            (graph, active_includes):
              - graph: adjacency list of files actually followed
                (basename -> list of header basenames).
              - active_includes: full set of header paths that were
                followed. Empty if active_macros is None.
        """
        # Local import to avoid a hard dependency at module import time
        # (the pruner's test suite loads dep_graph without expr_eval
        # being exercised).
        from expr_eval import ExpressionEvaluator

        self.graph = {}
        self.file_map = {}
        self.resolved_paths = {}

        if active_macros is None:
            active_macros = {}

        evaluator = ExpressionEvaluator(active_macros)
        include_dirs = include_dirs or []
        resolved = self._resolve_path(root_file, include_dirs)
        if not resolved:
            return self.graph, set()

        # cond_visited = full set of resolved paths followed (for cycle
        # detection); active_includes = subset actually emitted to graph
        cond_visited: Set[str] = set()
        active_includes: Set[str] = set()
        self._traverse_conditional(
            resolved, include_dirs, cond_visited, active_includes,
            max_depth, 0, evaluator,
        )
        return self.graph, active_includes

    def _traverse_conditional(
        self,
        file_path: str,
        include_dirs: List[str],
        visited: Set[str],
        active_includes: Set[str],
        max_depth: int,
        depth: int,
        evaluator,
    ):
        if depth >= max_depth:
            return
        if file_path in visited:
            return
        visited.add(file_path)

        basename = os.path.basename(file_path)
        self.graph[basename] = []
        self.resolved_paths[basename] = file_path

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except (IOError, OSError):
            return

        # `if_stack` holds the directive-evaluated-as-boolean state for
        # each currently-open #if block. Top of stack = innermost.
        # An include is followed only if all entries are True.
        if_stack: List[bool] = []

        for line in content.splitlines():
            dm = _DIRECTIVE_RE.match(line)
            if dm:
                directive = dm.group(1)
                rest = dm.group(2)
                if directive in ("ifdef", "ifndef", "if"):
                    cond = rest.strip()
                    if directive == "ifndef":
                        evaluated = not evaluator.evaluate(cond)
                    else:
                        evaluated = evaluator.evaluate(cond)
                    # If any ancestor is inactive, this block is also
                    # inactive (we never enter inactive code semantically).
                    if if_stack and not all(if_stack):
                        evaluated = False
                    if_stack.append(bool(evaluated))
                    continue
                if directive == "elif":
                    # For simplicity we don't try to track which branch
                    # of an if/elif chain was taken. If we entered the
                    # first #if as active and the elif conditions are
                    # false, the chain flips to inactive.
                    if not if_stack:
                        continue
                    if all(if_stack[:-1]):
                        # Re-evaluate. But once an if/elif has matched,
                        # subsequent elif branches should be inactive.
                        # We don't track "taken" here — leave as best-effort.
                        cond = rest.strip()
                        evaluated = evaluator.evaluate(cond)
                        # If the previous branch was active, this elif is
                        # inactive (semantics: at most one branch wins).
                        if if_stack[-1]:
                            if_stack[-1] = False
                        else:
                            if_stack[-1] = bool(evaluated)
                    continue
                if directive == "else":
                    if not if_stack:
                        continue
                    # Active only if ancestors are all active AND the
                    # previous branch was inactive.
                    if all(if_stack[:-1]) and not if_stack[-1]:
                        if_stack[-1] = True
                    else:
                        if_stack[-1] = False
                    continue
                if directive == "endif":
                    if if_stack:
                        if_stack.pop()
                    continue

            # Not a preprocessor directive — try include match.
            im = _INCLUDE_RE.match(line)
            if not im:
                continue
            header_name = im.group(1)
            # An include is followed only if all enclosing #if blocks
            # are active. An empty if_stack (no enclosing #if) also
            # counts as "all active" (vacuously true).
            if if_stack and not all(if_stack):
                # Skip — but still record the header name in the
                # adjacency list so the caller can see that we noticed
                # the include (and chose not to follow it).
                self.graph[basename].append(header_name + " [skipped]")
                continue

            self.graph[basename].append(header_name)
            header_path = self._resolve_path(header_name, include_dirs, file_path)
            if header_path and header_path not in visited:
                active_includes.add(header_path)
                self._traverse_conditional(
                    header_path, include_dirs, visited, active_includes,
                    max_depth, depth + 1, evaluator,
                )

    # ── Shared utilities ────────────────────────────────────────────

    def _resolve_path(
        self,
        name: str,
        include_dirs: List[str],
        relative_to: Optional[str] = None,
    ) -> Optional[str]:
        if os.path.isabs(name) and os.path.isfile(name):
            return name

        if relative_to:
            candidate = os.path.join(os.path.dirname(relative_to), name)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)

        for inc_dir in include_dirs:
            abs_inc_dir = (
                inc_dir if os.path.isabs(inc_dir) else os.path.abspath(inc_dir)
            )
            candidate = os.path.join(abs_inc_dir, name)
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)

        return None

    # ── Pretty printers (unchanged) ─────────────────────────────────

    def to_json(self, root_file: Optional[str] = None) -> str:
        import json

        if root_file:
            root_basename = os.path.basename(root_file)
            data = {
                "root": root_basename,
                "nodes": [],
                "edges": [],
            }
            seen = set()
            stack = [root_basename]
            while stack:
                node = stack.pop()
                if node in seen:
                    continue
                seen.add(node)
                data["nodes"].append(node)
                deps = self.graph.get(node, [])
                for dep in deps:
                    data["edges"].append({"from": node, "to": dep})
                    if dep not in seen:
                        stack.append(dep)
            return json.dumps(data, indent=2)

        data = {"nodes": [], "edges": []}
        all_nodes = set()
        for node, deps in self.graph.items():
            all_nodes.add(node)
            for dep in deps:
                all_nodes.add(dep)
                data["edges"].append({"from": node, "to": dep})
        data["nodes"] = sorted(all_nodes)
        return json.dumps(data, indent=2)

    def to_dot(self, root_file: Optional[str] = None) -> str:
        lines = ["digraph G {", '  rankdir="LR";', "  node [shape=box];"]
        seen = set()

        if root_file:
            root_basename = os.path.basename(root_file)
            lines.append(f'  "{root_basename}" [style=filled, fillcolor=lightblue];')

        for node, deps in self.graph.items():
            seen.add(node)
            for dep in deps:
                seen.add(dep)
                lines.append(f'  "{node}" -> "{dep}";')

        for node in seen:
            label = node.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if node not in self.graph:
                lines.append(f'  "{node}" [style=dashed];')

        lines.append("}")
        return "\n".join(lines)

    def get_stats(self, root_file: str) -> Dict[str, int]:
        root_basename = os.path.basename(root_file)
        seen = set()
        stack = [root_basename]
        max_depth = 0
        depth_map = {root_basename: 0}

        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            current_depth = depth_map.get(node, 0)
            max_depth = max(max_depth, current_depth)
            deps = self.graph.get(node, [])
            for dep in deps:
                if dep not in seen:
                    depth_map[dep] = current_depth + 1
                    stack.append(dep)

        return {
            "total_files": len(seen),
            "max_depth": max_depth,
            "total_edges": sum(len(deps) for deps in self.graph.values()),
        }
