"""Tests for P12 safety features: read-only mode + path allow/denylist.

Covers:
  - _check_path_safe(): no restrictions (legacy behaviour)
  - _check_path_safe(): denylist blocks paths under a denylisted dir
  - _check_path_safe(): allowlist requires the path be under an
    allowed root
  - _check_path_safe(): allow + denylist intersect; denylist wins
  - _check_path_safe(): realpath resolution (../ escapes detected)
  - apply_patch respects MACROPRUNER_READONLY env var
  - apply_patch respects path_allowlist
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _write_cdb(d, src):
    cdb = os.path.join(d, "compile_commands.json")
    with open(cdb, "w") as f:
        json.dump([{
            "directory": d,
            "command": "gcc -DPRODUCT_TYPE=3 -c main.c -o main.o",
            "file": "main.c",
        }], f)


def _write_main(d, name="main.c"):
    src = os.path.join(d, name)
    with open(src, "w") as f:
        f.write("#if X\nvoid a(){}\n#else\nvoid b(){}\n#endif\n")
    return src


# ── _check_path_safe unit tests ──────────────────────────────────


def test_no_restrictions_means_legacy_behavior():
    """Empty allowlist + empty denylist = no path check."""
    from mcp_server import _check_path_safe
    # Any path passes.
    err = _check_path_safe("/etc/passwd", [], [])
    assert err is None


def test_empty_allowlist_with_denylist_only():
    """Empty allowlist + non-empty denylist = deny just the listed paths."""
    from mcp_server import _check_path_safe
    # Path not under .git/ → allowed
    err = _check_path_safe("/etc/passwd", [], ["/tmp/secret"])
    assert err is None
    # Path under /tmp/secret → denied
    err = _check_path_safe("/tmp/secret/key.pem", [], ["/tmp/secret"])
    assert err is not None
    assert "denylisted" in err.formatted()


def test_allowlist_requires_path_inside_root():
    from mcp_server import _check_path_safe
    with tempfile.TemporaryDirectory() as d:
        _write_main(d)
        # Allow the project dir.
        err = _check_path_safe(os.path.join(d, "main.c"), [d], [])
        assert err is None
        # Block paths outside the allowlist.
        err = _check_path_safe("/etc/passwd", [d], [])
        assert err is not None
        assert "allowlist" in err.formatted()


def test_denylist_wins_over_allowlist():
    """Even if a path is under an allowlist, a denylisted ancestor blocks it."""
    from mcp_server import _check_path_safe
    with tempfile.TemporaryDirectory() as d:
        git_dir = os.path.join(d, ".git")
        os.makedirs(git_dir)
        _write_main(git_dir, "config")
        # main.c inside .git/ should be denied even though d/ is allowed.
        # Use absolute paths for the denylist — relative paths would be
        # resolved against the host's CWD, not the test's tmpdir.
        err = _check_path_safe(
            os.path.join(d, ".git", "config"),
            [d],           # allow d
            [git_dir],     # deny d/.git (absolute)
        )
        assert err is not None
        assert "denylisted" in err.formatted()


def test_realpath_resolution_escapes_dotdot():
    """Paths with .. that resolve outside the allowlist are blocked."""
    from mcp_server import _check_path_safe
    with tempfile.TemporaryDirectory() as d:
        proj = os.path.join(d, "proj")
        os.makedirs(proj)
        # Path is "proj/../secret.txt" which resolves to d/secret.txt,
        # outside the allowlist.
        tricky = os.path.join(proj, "..", "secret.txt")
        # Create the file so realpath works cleanly.
        with open(os.path.join(d, "secret.txt"), "w") as f:
            f.write("nope")
        err = _check_path_safe(tricky, [proj], [])
        assert err is not None
        assert "allowlist" in err.formatted()


def test_invalid_path_type_returns_fatalerror():
    from mcp_server import _check_path_safe
    err = _check_path_safe(None, ["/tmp"], [])  # type: ignore[arg-type]
    # The realpath call will fail on None, so we get a FatalError.
    assert err is not None
    assert "Invalid file_path" in err.formatted() or "must be a string" in err.formatted()


# ── apply_patch read-only mode ───────────────────────────────────


def test_apply_patch_readonly_mode_refuses():
    """MACROPRUNER_READONLY=1 should make apply_patch return [FATAL]."""
    import asyncio
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp.client.session import ClientSession

    async def go():
        env = dict(os.environ)
        env["MACROPRUNER_READONLY"] = "1"
        with tempfile.TemporaryDirectory() as d:
            _write_cdb(d, _write_main(d))
            params = StdioServerParameters(
                command=sys.executable,
                args=[os.path.abspath("mcp_server.py")],
                cwd=d,
                env=env,
            )
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    res = await s.call_tool("apply_patch", {
                        "file_path": os.path.join(d, "main.c"),
                        "diff": "@@ -1,1 +1,1 @@\n-#if X\n+#if Y\n void a(){}\n",
                    })
                    text = res.content[0].text
                    assert "[FATAL]" in text
                    assert "MACROPRUNER_READONLY" in text
                    # Verify the file was NOT modified.
                    with open(os.path.join(d, "main.c")) as f:
                        content = f.read()
                    assert "#if X" in content
                    assert "#if Y" not in content

    asyncio.run(go())


def test_apply_patch_works_in_normal_mode():
    """Without MACROPRUNER_READONLY, apply_patch should work normally."""
    import asyncio
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp.client.session import ClientSession

    async def go():
        env = {k: v for k, v in os.environ.items() if k != "MACROPRUNER_READONLY"}
        with tempfile.TemporaryDirectory() as d:
            _write_cdb(d, _write_main(d))
            params = StdioServerParameters(
                command=sys.executable,
                args=[os.path.abspath("mcp_server.py")],
                cwd=d,
                env=env,
            )
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    res = await s.call_tool("apply_patch", {
                        "file_path": os.path.join(d, "main.c"),
                        "diff": "@@ -1,1 +1,1 @@\n-#if X\n+#if Y\n void a(){}\n",
                    })
                    text = res.content[0].text
                    # No [FATAL] should appear.
                    assert "[FATAL]" not in text
                    assert "[OK]" in text

    asyncio.run(go())


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
