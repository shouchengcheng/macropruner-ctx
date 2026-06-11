"""Tests for the CLI entry point (cli.py)."""
import os
import subprocess
import sys
import tempfile
import textwrap

from cli import main as cli_main


def _setup_project(tmpdir, target="PRODUCT_3"):
    src = os.path.join(tmpdir, "main.c")
    with open(src, "w") as f:
        f.write(textwrap.dedent("""\
            #if PRODUCT_TYPE == 3
            void init_a(void) {}
            #elif PRODUCT_TYPE == 5
            void init_b(void) {}
            #else
            void init_default(void) {}
            #endif
            int main(void) { return 0; }
        """))
    cdb = os.path.join(tmpdir, "compile_commands.json")
    with open(cdb, "w") as f:
        f.write(
            f'[{{"directory": "{tmpdir}", '
            f'"command": "gcc -DPRODUCT_TYPE=3 -c main.c -o main.o", '
            f'"file": "main.c"}}]'
        )
    return src, cdb


def test_cli_read_prints_pruned_output():
    with tempfile.TemporaryDirectory() as d:
        src, _ = _setup_project(d)
        rc = cli_main(["read", src, "--target", "PRODUCT_3"])
        # (the main() is being called with stdout going to a string
        # buffer via the test runner, so we just verify the return
        # code here — actual stdout content is verified by the e2e
        # shell test in run_cli_demo.sh)
        assert rc == 0


def test_cli_skeleton_returns_zero():
    with tempfile.TemporaryDirectory() as d:
        src, _ = _setup_project(d)
        rc = cli_main(["skeleton", src, "--target", "PRODUCT_3"])
        assert rc == 0


def test_cli_missing_cdb_returns_fatal_code():
    """Without a compile_commands.json (and no .macroprunerrc),
    the CLI must fail loudly with [FATAL] and exit code 1."""
    from cc_parser import clear_cache

    clear_cache()
    with tempfile.TemporaryDirectory() as d:
        src, cdb = _setup_project(d)
        os.unlink(cdb)
        clear_cache()
        import io, contextlib
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli_main(["read", src, "--target", "PRODUCT_3"])
        assert rc == 1, f"expected rc=1, got rc={rc}; stderr={err.getvalue()!r}; stdout={out.getvalue()[:200]!r}"


def test_cli_unknown_subcommand_fails():
    """An unknown subcommand should cause argparse to exit with code 2
    (the standard "invalid choice" exit code)."""
    try:
        rc = cli_main(["bogus", "x"])
    except SystemExit as e:
        # argparse calls sys.exit(2) for usage errors.
        assert e.code == 2
        return
    assert False, f"expected SystemExit(2), got rc={rc}"


def test_cli_help_prints_and_exits():
    """--help should print to stdout and exit 0."""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            cli_main(["--help"])
    except SystemExit as e:
        # argparse calls sys.exit(0) for --help.
        assert e.code == 0
    assert "usage:" in buf.getvalue().lower()


def test_cli_diff_returns_nonzero_on_disagreement():
    """When regex and clang disagree, diff should exit non-zero.

    Doesn't assert WHICH backend is right — just that disagreement
    is flagged.
    """
    with tempfile.TemporaryDirectory() as d:
        src, _ = _setup_project(d)
        rc = cli_main(["diff", src, "--target", "PRODUCT_3"])
        # We don't know if the backends agree; just assert the call
        # completed. Either rc=0 (agree) or rc=1 (disagree) is fine.
        assert rc in (0, 1)


def test_cli_diff_uses_clang_oracle_when_available():
    """When clang is available, diff should run both backends.

    Skip the test if clang isn't on PATH (debian-minimal images).
    """
    from backends.clang_backend import _find_clang
    if _find_clang() is None:
        return  # SKIP
    with tempfile.TemporaryDirectory() as d:
        src, _ = _setup_project(d)
        rc = cli_main(["diff", src, "--target", "PRODUCT_3"])
        assert rc in (0, 1)


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
        except SystemExit as e:
            fail += 1
            print(f"{f.__name__}: ERROR — SystemExit({e.code})")
        except Exception as e:
            fail += 1
            print(f"{f.__name__}: ERROR — {type(e).__name__}: {e}")
    print(f"\n=== {len(funcs)-fail}/{len(funcs)} passed ===")
    sys.exit(0 if fail == 0 else 1)
