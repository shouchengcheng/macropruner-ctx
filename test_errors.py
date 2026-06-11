"""Tests for the error classification and formatting."""
import os
import sys

from errors import (
    MacroPrunerError,
    FatalError,
    TransientError,
    format_error,
    with_fallback,
)


def test_fatal_formatted_includes_tag_and_hint():
    e = FatalError("bad path", hint="check cwd")
    out = e.formatted()
    assert "[FATAL]" in out
    assert "bad path" in out
    assert "check cwd" in out


def test_transient_formatted_includes_warn_tag():
    e = TransientError("partial output")
    out = e.formatted()
    assert "[WARN]" in out
    assert "partial output" in out


def test_format_error_maps_filenotfound_to_fatal():
    e = FileNotFoundError("/no/such/file.c")
    out = format_error(e)
    assert "[FATAL]" in out
    assert "/no/such/file.c" in out
    # Should include a hint.
    assert "hint:" in out


def test_format_error_maps_valueerror_to_fatal():
    out = format_error(ValueError("nope"))
    assert "[FATAL]" in out


def test_format_error_maps_permissionerror_to_fatal():
    out = format_error(PermissionError("denied"))
    assert "[FATAL]" in out
    assert "permissions" in out


def test_format_error_unknown_exception_tagged_generic():
    class WeirdError(Exception):
        pass

    out = format_error(WeirdError("??"))
    assert "[ERROR]" in out
    assert "WeirdError" in out
    assert "??" in out


def test_with_fallback_returns_value_on_transient():
    def bad():
        raise TransientError("soft")

    assert with_fallback(bad, fallback_value="DEFAULT") == "DEFAULT"


def test_with_fallback_propagates_fatal():
    def bad():
        raise FatalError("hard")

    raised = False
    try:
        with_fallback(bad, fallback_value="DEFAULT")
    except FatalError:
        raised = True
    assert raised


def test_with_fallback_propagates_unknown_exception():
    def bad():
        raise RuntimeError("unexpected")

    # Unknown exceptions in per-dep loops: with_fallback returns
    # the fallback (warns, doesn't kill the loop).
    assert with_fallback(bad, fallback_value="SAFE") == "SAFE"


def test_with_fallback_returns_normal_value():
    def good():
        return 42
    assert with_fallback(good, fallback_value="SAFE") == 42


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
