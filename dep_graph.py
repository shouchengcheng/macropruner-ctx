"""
C/C++ Dependency Graph Builder - Parses #include directives and builds a dependency graph.

Outputs: JSON adjacency list or DOT format for visualization.
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set


class DependencyGraph:
    INCLUDE_PATTERN = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')

    def __init__(self):
        self.graph: Dict[str, List[str]] = {}
        self.file_map: Dict[str, str] = {}
        self.resolved_paths: Dict[str, str] = {}

    def build(
        self,
        root_file: str,
        include_dirs: Optional[List[str]] = None,
        max_depth: int = 10,
    ) -> Dict[str, List[str]]:
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
