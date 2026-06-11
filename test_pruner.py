"""
Test suite for the Conditional Compilation Pruner.
Validates stack-based state machine behavior with nested conditions.
"""

from pruner_core import PrunerCore, PrunerMode, prune_source


def test_simple_ifdef_active():
    """Test simple #ifdef with active macro."""
    source = """#include <stdio.h>
#ifdef DEBUG
printf("debug mode");
#endif
int main() { return 0; }"""

    macros = {"DEBUG": None}
    pruned, stats = prune_source(source, macros, PrunerMode.VIRTUAL_FOLDING)

    assert 'printf("debug mode")' in pruned
    assert stats["reduction_percentage"] == 0
    print(f"test_simple_ifdef_active: PASS - {stats}")


def test_simple_ifdef_inactive():
    """Test simple #ifdef with inactive macro."""
    source = """#include <stdio.h>
#ifdef DEBUG
printf("debug mode");
#endif
int main() { return 0; }"""

    macros = {}
    pruned, stats = prune_source(source, macros, PrunerMode.VIRTUAL_FOLDING)

    assert 'printf("debug mode")' not in pruned or "[INACTIVE]" in pruned
    print(f"test_simple_ifdef_inactive: PASS - {stats}")


def test_nested_conditions():
    """Test deeply nested #ifdef blocks."""
    source = """#ifdef FEATURE_A
code_a();
#ifdef FEATURE_B
code_b();
#ifdef FEATURE_C
code_c();
#endif
code_b2();
#endif
code_a2();
#endif"""

    macros = {"FEATURE_A": None, "FEATURE_B": None}
    pruned, stats = prune_source(source, macros, PrunerMode.VIRTUAL_FOLDING)

    assert "code_a()" in pruned
    assert "code_b()" in pruned
    assert "code_c()" not in pruned or "[INACTIVE]" in pruned
    assert "code_b2()" in pruned
    assert "code_a2()" in pruned
    print(f"test_nested_conditions: PASS - {stats}")


def test_else_toggle():
    """Test #else branch activation."""
    source = """#ifdef RELEASE
mode = "release";
#else
mode = "debug";
#endif"""

    macros = {}
    pruned, stats = prune_source(source, macros, PrunerMode.VIRTUAL_FOLDING)

    assert 'mode = "debug"' in pruned
    assert 'mode = "release"' not in pruned or "[INACTIVE]" in pruned
    print(f"test_else_toggle: PASS - {stats}")


def test_physical_deletion():
    """Test physical deletion mode removes lines entirely."""
    source = """line1
#ifdef REMOVED
removed_line
#endif
line2"""

    macros = {}
    pruned, stats = prune_source(source, macros, PrunerMode.PHYSICAL_DELETION)

    assert "removed_line" not in pruned
    assert stats["removed_lines"] > 0
    print(f"test_physical_deletion: PASS - {stats}")


def test_elif_chain():
    """Test #elif chain evaluation."""
    source = """#ifdef PLATFORM_A
platform = "A";
#elif defined(PLATFORM_B)
platform = "B";
#else
platform = "unknown";
#endif"""

    macros = {"PLATFORM_B": None}
    pruned, stats = prune_source(source, macros, PrunerMode.VIRTUAL_FOLDING)

    assert 'platform = "B"' in pruned
    assert 'platform = "A"' not in pruned or "[INACTIVE]" in pruned
    print(f"test_elif_chain: PASS - {stats}")


def test_ifndef():
    """Test #ifndef directive."""
    source = """#ifndef GUARD
guard_not_set = true;
#endif"""

    macros = {}
    pruned, stats = prune_source(source, macros, PrunerMode.VIRTUAL_FOLDING)

    assert "guard_not_set = true" in pruned
    print(f"test_ifndef: PASS - {stats}")


if __name__ == "__main__":
    test_simple_ifdef_active()
    test_simple_ifdef_inactive()
    test_nested_conditions()
    test_else_toggle()
    test_physical_deletion()
    test_elif_chain()
    test_ifndef()
    print("\nAll tests passed!")
