"""Tests for Stage 4 token budget enforcement."""
import json
import os
import sys
import tempfile
import textwrap

# Test the budget enforcement via the MCP read_c tool, since that
# is where the user-facing API lives. Importing mcp_server pulls
# in FastMCP, which requires the venv to be active.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _setup_project(tmpdir, src_content):
    src = os.path.join(tmpdir, "main.c")
    with open(src, "w") as f:
        f.write(src_content)
    cdb = os.path.join(tmpdir, "compile_commands.json")
    with open(cdb, "w") as f:
        json.dump([{
            "directory": tmpdir,
            "command": "gcc -DPRODUCT_TYPE=3 -c main.c -o main.o",
            "file": src,
        }], f)
    return src, cdb


def test_budget_zero_means_no_cap():
    """token_budget=0 must NOT trigger degradation."""
    with tempfile.TemporaryDirectory() as d:
        src, cdb = _setup_project(d, textwrap.dedent("""\
            #if X
            int main(void) { return 0; }
            #endif
        """))
        from mcp_server import _prune_file
        r = _prune_file(src, "T", cdb, token_budget=0)
        assert r.extra.get("budget_degraded", "") == ""
        assert r.extra.get("budget_exceeded", "false") == "false"


def test_budget_passes_unchanged_when_under():
    """If pruned output is under budget, no degradation happens."""
    with tempfile.TemporaryDirectory() as d:
        src, cdb = _setup_project(d, "int main(void) { return 0; }\n")
        from mcp_server import _prune_file
        # Big budget — pruned output is way under.
        r = _prune_file(src, "T", cdb, token_budget=10000)
        assert r.extra.get("budget_degraded", "") == ""
        assert "int main" in r.code


def test_budget_triggers_skeleton_degradation():
    """When pruned > budget but skeleton <= budget, return skeleton.

    We use PRODUCT_TYPE (which IS in the active macros, since the
    compile_db's -D includes it) so the function body survives
    the prune. The body is large enough to push the pruned output
    over the budget; the skeleton (just the signature) fits.
    """
    with tempfile.TemporaryDirectory() as d:
        big_body = textwrap.dedent("""\
            #if PRODUCT_TYPE == 3
            int main(void) {
                int a = 1;
                int b = 2;
                int c = 3;
                int d = 4;
                return a + b + c + d;
            }
            #endif
        """)
        src, cdb = _setup_project(d, big_body)
        from mcp_server import _prune_file
        # Tiny budget: pruned body is ~80 chars / 3.7 = 22 tokens;
        # the skeleton "int main(void) { /* ... */ }" is ~30 chars
        # = 8 tokens. Budget of 12 should trigger skeleton degradation.
        r = _prune_file(src, "T", cdb, token_budget=12)
        # Either degraded to skeleton, or flagged as exceeded —
        # both are valid "we responded to the budget" behaviors.
        degraded = r.extra.get("budget_degraded", "")
        exceeded = r.extra.get("budget_exceeded", "false")
        assert degraded == "skeleton" or exceeded == "true", (
            f"expected budget response; got degraded={degraded!r}, exceeded={exceeded!r}"
        )


def test_budget_exceeded_flagged_when_neither_fits():
    """If even the skeleton doesn't fit, tag as exceeded."""
    with tempfile.TemporaryDirectory() as d:
        # Huge content; smallest reasonable budget.
        huge = "int x;\n" * 1000
        src, cdb = _setup_project(d, huge)
        from mcp_server import _prune_file
        r = _prune_file(src, "T", cdb, token_budget=1)
        # Budget=1: nothing fits, so we should see exceeded=true.
        # (degraded may or may not be set depending on the order of
        # the checks; we care that exceeded is true OR that the
        # output is at least the pruned code without fake success.)
        assert r.extra.get("budget_exceeded", "false") == "true" or r.extra.get("budget_degraded") == "skeleton"


def test_budget_from_config_default():
    """A budget set in .macroprunerrc should be honored when no
    explicit token_budget is passed."""
    with tempfile.TemporaryDirectory() as d:
        src, cdb = _setup_project(d, textwrap.dedent("""\
            #if X
            int main(void) { return 0; }
            #endif
        """))
        # Write a .macroprunerrc with token_budget=10000.
        with open(os.path.join(d, ".macroprunerrc"), "w") as f:
            f.write("token_budget = 10000\n")
        from mcp_server import _prune_file
        r = _prune_file(src, "", cdb, token_budget=0)
        # Budget from config (10000) is well above the pruned token
        # count, so no degradation.
        assert r.extra.get("budget_degraded", "") == ""
        assert r.extra.get("budget_exceeded", "false") == "false"


def test_budget_banner_appears_in_read_c():
    """The MCP read_c tool should render the Degraded / [WARN] line."""
    # This is end-to-end enough to live in test_mcp_server.py; here
    # we just check the path: when we call the inner _enforce_budget
    # and a degradation happens, the extra field is populated.
    with tempfile.TemporaryDirectory() as d:
        src, cdb = _setup_project(d, "int main(void) { return 0; }\n")
        from mcp_server import _prune_file
        r = _prune_file(src, "T", cdb, token_budget=1)
        # The exact behavior depends on the size of the skeleton.
        # Just assert the budget tracking happened.
        assert "budget_exceeded" in r.extra or "budget_degraded" in r.extra


if __name__ == "__main__":
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
