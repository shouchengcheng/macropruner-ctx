"""Tests for Stage 3 Phase 2 — conditional-aware include resolution."""
import os
import tempfile
import textwrap

from dep_graph import DependencyGraph


def _write_tree(tmpdir, files):
    """Create files in tmpdir. `files` = {relpath: content}."""
    for rel, content in files.items():
        path = os.path.join(tmpdir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(textwrap.dedent(content))


def test_unconditional_follows_all_includes():
    with tempfile.TemporaryDirectory() as d:
        _write_tree(d, {
            "main.c": """
                #include "always.h"
                #include "maybe_a.h"
                #include "maybe_b.h"
            """,
            "always.h": "int always_var;\n",
            "maybe_a.h": "int a_var;\n",
            "maybe_b.h": "int b_var;\n",
        })
        dg = DependencyGraph()
        g, active = dg.conditional_build(
            os.path.join(d, "main.c"), include_dirs=[d], active_macros={}
        )
        # Empty macros = everything is "active" (no #if blocks).
        assert "always.h" in g["main.c"]
        assert "maybe_a.h" in g["main.c"]
        assert "maybe_b.h" in g["main.c"]


def test_ifdef_active_follows_include():
    with tempfile.TemporaryDirectory() as d:
        _write_tree(d, {
            "main.c": """
                #ifdef HAS_WIFI
                #include "wifi.h"
                #endif
            """,
            "wifi.h": "void wifi_init(void);\n",
        })
        dg = DependencyGraph()
        g, active = dg.conditional_build(
            os.path.join(d, "main.c"), include_dirs=[d],
            active_macros={"HAS_WIFI": None},
        )
        assert "wifi.h" in g["main.c"]
        assert "wifi.h [skipped]" not in g["main.c"]
        assert any("wifi.h" in p for p in active)


def test_ifdef_inactive_skips_include():
    with tempfile.TemporaryDirectory() as d:
        _write_tree(d, {
            "main.c": """
                #ifdef HAS_WIFI
                #include "wifi.h"
                #endif
                #include "common.h"
            """,
            "wifi.h": "void wifi_init(void);\n",
            "common.h": "int common;\n",
        })
        dg = DependencyGraph()
        g, active = dg.conditional_build(
            os.path.join(d, "main.c"), include_dirs=[d],
            active_macros={},  # HAS_WIFI NOT defined
        )
        assert "wifi.h [skipped]" in g["main.c"]
        assert "common.h" in g["main.c"]
        assert not any("wifi.h" in p for p in active)


def test_nested_ifdef_inner_active():
    with tempfile.TemporaryDirectory() as d:
        _write_tree(d, {
            "main.c": """
                #ifdef PRODUCT_A
                #  ifdef HAS_BLE
                #    include "a_ble.h"
                #  else
                #    include "a_no_ble.h"
                #  endif
                #endif
            """,
            "a_ble.h": "int ble;\n",
            "a_no_ble.h": "int no_ble;\n",
        })
        dg = DependencyGraph()
        g, active = dg.conditional_build(
            os.path.join(d, "main.c"), include_dirs=[d],
            active_macros={"PRODUCT_A": None, "HAS_BLE": None},
        )
        assert "a_ble.h" in g["main.c"]
        assert "a_no_ble.h [skipped]" in g["main.c"]


def test_nested_ifdef_outer_inactive_skips_inner():
    with tempfile.TemporaryDirectory() as d:
        _write_tree(d, {
            "main.c": """
                #ifdef PRODUCT_B
                #  ifdef HAS_BLE
                #    include "b_ble.h"
                #  endif
                #endif
            """,
            "b_ble.h": "int b_ble;\n",
        })
        dg = DependencyGraph()
        g, active = dg.conditional_build(
            os.path.join(d, "main.c"), include_dirs=[d],
            active_macros={"HAS_BLE": None},  # PRODUCT_B not defined
        )
        # Outer inactive => inner include should be skipped too
        assert "b_ble.h [skipped]" in g["main.c"]
        assert not any("b_ble.h" in p for p in active)


def test_if_expression_value():
    with tempfile.TemporaryDirectory() as d:
        _write_tree(d, {
            "main.c": """
                #if PRODUCT_TYPE == 3
                #include "p3.h"
                #elif PRODUCT_TYPE == 5
                #include "p5.h"
                #endif
            """,
            "p3.h": "int p3;\n",
            "p5.h": "int p5;\n",
        })
        dg = DependencyGraph()
        g, active = dg.conditional_build(
            os.path.join(d, "main.c"), include_dirs=[d],
            active_macros={"PRODUCT_TYPE": "3"},
        )
        assert "p3.h" in g["main.c"]
        # p5.h should be in the adjacency list but marked as skipped
        # (we still record the include was seen, just not followed).
        assert "p5.h" in g["main.c"] or "p5.h [skipped]" in g["main.c"]


def test_endif_does_not_crash_on_unbalanced():
    with tempfile.TemporaryDirectory() as d:
        _write_tree(d, {
            "main.c": """
                #include "a.h"
                #endif
                #include "b.h"
            """,
            "a.h": "int a;\n",
            "b.h": "int b;\n",
        })
        dg = DependencyGraph()
        g, active = dg.conditional_build(
            os.path.join(d, "main.c"), include_dirs=[d], active_macros={}
        )
        # No crash, both still listed
        assert "a.h" in g["main.c"] or "a.h [skipped]" in g["main.c"]
        assert "b.h" in g["main.c"]


if __name__ == "__main__":
    import sys
    funcs = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    fail = 0
    for f in funcs:
        try:
            f()
            print(f"{f.__name__}: PASS")
        except AssertionError as e:
            fail += 1
            print(f"{f.__name__}: FAIL — {e}")
        except Exception as e:
            fail += 1
            print(f"{f.__name__}: ERROR — {type(e).__name__}: {e}")
    print(f"\n=== {len(funcs)-fail}/{len(funcs)} passed ===")
    sys.exit(0 if fail == 0 else 1)
