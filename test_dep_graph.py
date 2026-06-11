"""Unit tests for dep_graph.py"""

import json
import os
import sys
import tempfile

sys.path.insert(0, ".")

from dep_graph import DependencyGraph


def _create_temp_files():
    tmpdir = tempfile.mkdtemp()
    include_dir = os.path.join(tmpdir, "include")
    os.makedirs(include_dir, exist_ok=True)

    main_c = os.path.join(tmpdir, "main.c")
    with open(main_c, "w") as f:
        f.write(
            '#include "utils.h"\n'
            '#include "network.h"\n'
            "\n"
            "int main() {\n"
            "    return 0;\n"
            "}\n"
        )

    utils_h = os.path.join(tmpdir, "utils.h")
    with open(utils_h, "w") as f:
        f.write('#include <stdio.h>\n#include "config.h"\n\nvoid print_hello(void);\n')

    config_h = os.path.join(tmpdir, "config.h")
    with open(config_h, "w") as f:
        f.write("#define MAX_SIZE 100\n")

    network_h = os.path.join(tmpdir, "network.h")
    with open(network_h, "w") as f:
        f.write('#include "utils.h"\nvoid init_network(void);\n')

    return tmpdir, main_c, utils_h, config_h, network_h, include_dir


def test_build_graph():
    tmpdir, main_c, utils_h, config_h, network_h, include_dir = _create_temp_files()
    dg = DependencyGraph()
    graph = dg.build(main_c, include_dirs=[tmpdir], max_depth=5)

    assert "main.c" in graph
    assert "utils.h" in graph["main.c"]
    assert "network.h" in graph["main.c"]
    print("TEST build_graph: PASS")


def test_include_resolution():
    tmpdir, main_c, utils_h, config_h, network_h, include_dir = _create_temp_files()
    dg = DependencyGraph()
    dg.build(main_c, include_dirs=[tmpdir], max_depth=5)

    assert "config.h" in dg.graph.get("utils.h", [])
    print("TEST include_resolution: PASS")


def test_to_json():
    tmpdir, main_c, utils_h, config_h, network_h, include_dir = _create_temp_files()
    dg = DependencyGraph()
    dg.build(main_c, include_dirs=[tmpdir], max_depth=5)

    json_str = dg.to_json(root_file=main_c)
    data = json.loads(json_str)

    assert data["root"] == "main.c"
    assert "main.c" in data["nodes"]
    assert "utils.h" in data["nodes"]
    assert any(e["from"] == "main.c" and e["to"] == "utils.h" for e in data["edges"])
    print("TEST to_json: PASS")


def test_to_dot():
    tmpdir, main_c, utils_h, config_h, network_h, include_dir = _create_temp_files()
    dg = DependencyGraph()
    dg.build(main_c, include_dirs=[tmpdir], max_depth=5)

    dot_str = dg.to_dot(root_file=main_c)
    assert "digraph G" in dot_str
    assert '"main.c" -> "utils.h"' in dot_str
    assert "fillcolor=lightblue" in dot_str
    print("TEST to_dot: PASS")


def test_stats():
    tmpdir, main_c, utils_h, config_h, network_h, include_dir = _create_temp_files()
    dg = DependencyGraph()
    dg.build(main_c, include_dirs=[tmpdir], max_depth=5)

    stats = dg.get_stats(main_c)
    assert stats["total_files"] >= 2
    assert stats["max_depth"] >= 1
    assert stats["total_edges"] >= 2
    print(f"TEST stats: PASS - {stats}")


def test_max_depth():
    tmpdir, main_c, utils_h, config_h, network_h, include_dir = _create_temp_files()
    dg = DependencyGraph()
    dg.build(main_c, include_dirs=[tmpdir], max_depth=1)

    stats = dg.get_stats(main_c)
    assert stats["max_depth"] <= 1
    print(f"TEST max_depth: PASS - {stats}")


def test_cycle_detection():
    tmpdir = tempfile.mkdtemp()
    a_c = os.path.join(tmpdir, "a.c")
    with open(a_c, "w") as f:
        f.write('#include "b.h"\n')
    b_h = os.path.join(tmpdir, "b.h")
    with open(b_h, "w") as f:
        f.write('#include "a.c"\n')

    dg = DependencyGraph()
    dg.build(a_c, include_dirs=[tmpdir], max_depth=5)

    assert "a.c" in dg.graph
    print("TEST cycle_detection: PASS")


def test_resolved_paths():
    tmpdir, main_c, utils_h, config_h, network_h, include_dir = _create_temp_files()
    dg = DependencyGraph()
    dg.build(main_c, include_dirs=[tmpdir], max_depth=5)

    assert "main.c" in dg.resolved_paths
    assert os.path.isabs(dg.resolved_paths["main.c"])
    assert dg.resolved_paths["main.c"] == os.path.abspath(main_c)

    assert "utils.h" in dg.resolved_paths
    assert os.path.isabs(dg.resolved_paths["utils.h"])
    assert dg.resolved_paths["utils.h"] == os.path.abspath(utils_h)

    assert "config.h" in dg.resolved_paths
    assert "network.h" in dg.resolved_paths
    print("TEST resolved_paths: PASS")


def test_resolved_paths_reset_on_rebuild():
    tmpdir1, main_c1, _, _, _, _ = _create_temp_files()
    tmpdir2, main_c2, _, _, _, _ = _create_temp_files()

    dg = DependencyGraph()
    dg.build(main_c1, include_dirs=[tmpdir1], max_depth=5)
    paths1 = dict(dg.resolved_paths)

    dg.build(main_c2, include_dirs=[tmpdir2], max_depth=5)
    assert dg.resolved_paths != paths1
    assert dg.resolved_paths["main.c"] == os.path.abspath(main_c2)
    print("TEST resolved_paths_reset: PASS")


if __name__ == "__main__":
    test_build_graph()
    test_include_resolution()
    test_to_json()
    test_to_dot()
    test_stats()
    test_max_depth()
    test_cycle_detection()
    test_resolved_paths()
    test_resolved_paths_reset_on_rebuild()
    print("\nAll dependency graph tests passed!")
