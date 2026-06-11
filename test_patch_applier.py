"""Tests for the standalone unified diff applier + C syntax checker."""
import sys

from patch_applier import (
    PatchError,
    apply_unified_diff,
    check_c_syntax,
    _parse_diff,
    _apply_hunk,
)


# ── Diff parsing ───────────────────────────────────────────────────


def test_parse_simple_hunk():
    diff = """--- a/foo.c
+++ b/foo.c
@@ -1,3 +1,4 @@
 line1
-line2
+line2-new
+line2-extra
 line3
"""
    hunks = _parse_diff(diff)
    assert len(hunks) == 1
    from_line, from_count, _to_line, to_count, body = hunks[0]
    assert from_line == 1
    assert from_count == 3
    assert to_count == 4
    # Body should have 5 lines: context, removed, two added, context.
    assert body.count("\n") + 1 == 5


def test_parse_multiple_hunks():
    diff = """@@ -1,2 +1,3 @@
 a
+insert
 b
@@ -10,2 +11,3 @@
 x
+y
 z
"""
    hunks = _parse_diff(diff)
    assert len(hunks) == 2
    assert hunks[0][0] == 1
    assert hunks[1][0] == 10


def test_parse_no_hunks_raises():
    try:
        _parse_diff("no diff here\njust text\n")
        assert False, "expected PatchError"
    except PatchError as e:
        assert "no hunks" in str(e)


def test_parse_handles_no_file_header():
    """LLM-generated diffs sometimes drop the --- / +++ headers."""
    diff = """@@ -5,2 +5,3 @@
 old
+new
 end
"""
    hunks = _parse_diff(diff)
    assert len(hunks) == 1
    assert hunks[0][0] == 5


# ── Patch application ──────────────────────────────────────────────


def test_apply_simple_insertion():
    original = "a\nb\nc\nd\n"
    diff = """@@ -2,2 +2,3 @@
 b
+INSERTED
 c
"""
    result = apply_unified_diff(original, diff)
    assert result == "a\nb\nINSERTED\nc\nd\n"


def test_apply_replacement():
    original = "a\nb\nc\n"
    diff = """@@ -2,1 +2,1 @@
-b
+B
"""
    result = apply_unified_diff(original, diff)
    assert result == "a\nB\nc\n"


def test_apply_deletion():
    """A deletion should always have at least one context line."""
    original = "a\nb\nc\n"
    # Drop line 2 (b) while keeping 'a' and 'c' as context.
    diff = """@@ -1,3 +1,2 @@
 a
-b
 c
"""
    result = apply_unified_diff(original, diff)
    assert result == "a\nc\n"


def test_apply_multiple_hunks():
    original = "line1\nline2\nline3\nline4\nline5\n"
    diff = """@@ -1,1 +1,2 @@
 line1
+insert1
@@ -4,1 +5,2 @@
 line4
+insert4
"""
    result = apply_unified_diff(original, diff)
    assert result == "line1\ninsert1\nline2\nline3\nline4\ninsert4\nline5\n"


def test_apply_preserves_no_trailing_newline():
    original = "a\nb\nc"  # no trailing newline
    diff = """@@ -2,1 +2,1 @@
-b
+B
"""
    result = apply_unified_diff(original, diff)
    assert result == "a\nB\nc"
    assert not result.endswith("\n")


def test_apply_offset_out_of_range_raises():
    original = "a\nb\n"
    diff = """@@ -10,1 +10,1 @@
-b
+B
"""
    try:
        apply_unified_diff(original, diff)
        assert False, "expected PatchError"
    except PatchError as e:
        assert "before file start" in str(e) or "out of range" in str(e) or "only" in str(e)


def test_apply_context_mismatch_raises():
    original = "a\nb\nc\n"
    diff = """@@ -2,1 +2,1 @@
-X
+B
"""
    try:
        apply_unified_diff(original, diff)
        assert False, "expected PatchError"
    except PatchError as e:
        assert "mismatch" in str(e).lower()


def test_apply_real_c_style_change():
    original = """int main(void) {
    return 0;
}
"""
    diff = """@@ -1,2 +1,3 @@
 int main(void) {
+    printf("hi\\n");
     return 0;
 }
"""
    result = apply_unified_diff(original, diff)
    assert 'printf("hi\\n");' in result
    assert "return 0;" in result


# ── Syntax check ───────────────────────────────────────────────────


def test_syntax_check_balanced():
    assert check_c_syntax("int main(void) { return 0; }") == []


def test_syntax_check_unbalanced_braces():
    warnings = check_c_syntax("int main(void) { return 0;")
    assert any("unbalanced braces" in w for w in warnings)


def test_syntax_check_unbalanced_ifdef():
    src = """#ifdef FOO
void f(void) { }
"""
    warnings = check_c_syntax(src)
    assert any("#if/#endif" in w for w in warnings)


def test_syntax_check_balanced_ifdef():
    src = """#ifdef FOO
void f(void) { }
#endif
"""
    assert check_c_syntax(src) == []


def test_syntax_check_orphan_else():
    src = """#else
void f(void) { }
"""
    warnings = check_c_syntax(src)
    assert any("#else" in w for w in warnings)


def test_syntax_check_handles_strings_and_comments():
    src = '''
/* {{{{ in comment, no real braces */
int main(void) {
    char *s = "}{";  /* } in string and comment */
    return 0;
}
'''
    assert check_c_syntax(src) == []


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
