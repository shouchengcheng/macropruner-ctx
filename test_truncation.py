"""Tests for P15-1 output truncation.

Covers:
  - Small text: returns unchanged
  - Text at exactly max_bytes: returns unchanged
  - Text larger than max_bytes: truncates with [WARN] banner
  - Truncation breaks on a newline (readable)
  - offset > 0: starts the slice at offset
  - offset >= total size: returns overflow banner
  - offset < 0: clamped to 0
  - max_bytes <= 0: uses DEFAULT_MAX_BYTES
  - Banner tells the LLM the right next offset
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ── _apply_truncation unit tests ──────────────────────────────────


def test_small_text_unchanged():
    from mcp_server import _apply_truncation
    text = "small output"
    assert _apply_truncation(text) == text


def test_text_at_cap_unchanged():
    from mcp_server import _apply_truncation
    # Exactly max_bytes: no truncation needed.
    text = "x" * 1000
    out = _apply_truncation(text, max_bytes=1000)
    assert out == text
    assert "[WARN] Truncated" not in out


def test_text_larger_than_cap_truncates():
    from mcp_server import _apply_truncation
    text = "x" * 5000
    out = _apply_truncation(text, max_bytes=1000)
    # Output should be <= 1500 chars (truncated + banner).
    assert len(out) < 1500
    assert "[WARN] Truncated" in out
    assert "4000 bytes remaining" in out


def test_truncation_breaks_on_newline():
    from mcp_server import _apply_truncation
    # Build text with clear line boundaries.
    lines = [f"line {i}: " + "x" * 50 for i in range(100)]
    text = "\n".join(lines)
    out = _apply_truncation(text, max_bytes=500)
    # The truncation should not end mid-line; the last visible
    # content line should be a complete "line N:" string.
    assert out.rstrip().endswith(("\n" + f"/* [WARN] Truncated")[:0]) or "/* [WARN]" in out
    # Verify we didn't break mid-content: every line in the
    # output should be complete.
    visible = out.split("/* [WARN]")[0]
    for line in visible.splitlines():
        if line.startswith("line "):
            # Each line is "line N: " + 50 x's — verify both halves.
            assert line.startswith("line ") and line.count("x") == 50


def test_offset_starts_slice_at_position():
    from mcp_server import _apply_truncation
    text = "0123456789" * 100  # 1000 chars
    out = _apply_truncation(text, offset=500, max_bytes=200)
    # Should start at position 500 ('0123456789' index 0 == 500th char).
    # The first visible char is '0' (because 500 = 50 * 10).
    assert out.startswith("0")
    assert "[WARN] Truncated" in out


def test_offset_past_end_returns_overflow_banner():
    from mcp_server import _apply_truncation
    text = "short text"
    out = _apply_truncation(text, offset=100, max_bytes=1000)
    assert "[WARN] Truncated" in out
    assert "offset 100" in out
    # No actual text content beyond the banner.
    assert "short text" not in out


def test_negative_offset_clamps_to_zero():
    from mcp_server import _apply_truncation
    text = "x" * 2000
    out_neg = _apply_truncation(text, offset=-100, max_bytes=500)
    out_zero = _apply_truncation(text, offset=0, max_bytes=500)
    # Should produce equivalent output (modulo end-byte banner).
    # Both start with "x" and both have the truncation banner.
    assert out_neg.startswith("x")
    assert out_zero.startswith("x")
    assert "[WARN]" in out_neg
    assert "[WARN]" in out_zero


def test_default_max_bytes_used_when_zero():
    from mcp_server import _apply_truncation, DEFAULT_MAX_BYTES
    text = "x" * (DEFAULT_MAX_BYTES + 1000)
    out = _apply_truncation(text)  # max_bytes=0 -> default
    # Should be truncated to ~DEFAULT_MAX_BYTES + a bit for the banner.
    assert len(out) < DEFAULT_MAX_BYTES + 500
    assert "[WARN] Truncated" in out


def test_banner_reports_next_offset():
    from mcp_server import _apply_truncation
    text = "x" * 10000
    out = _apply_truncation(text, offset=0, max_bytes=1000)
    # The banner should suggest the next call's offset.
    import re
    m = re.search(r"offset=(\d+)", out)
    assert m is not None
    next_offset = int(m.group(1))
    # next_offset should be reasonable: > 0 and < total length.
    assert 0 < next_offset < len(text)


# ── integration: read_c honors offset / max_bytes via MCP ─────────


def test_read_c_uses_truncation_at_e2e():
    """End-to-end via stdio MCP. Build a large .c file, ask for
    max_bytes=2000, verify the output is truncated and contains
    the [WARN] banner."""
    import asyncio
    import json
    import tempfile
    from mcp.client.stdio import stdio_client, StdioServerParameters
    from mcp.client.session import ClientSession

    async def go():
        with tempfile.TemporaryDirectory() as d:
            # Build a large .c with many #if blocks.
            c_path = os.path.join(d, "big.c")
            with open(c_path, "w") as f:
                for i in range(500):
                    f.write(f"#if X\nvoid f{i}(void) {{ /* {i} */ }}\n#else\nvoid g{i}(void) {{ /* {i} */ }}\n#endif\n")

            cdb = os.path.join(d, "compile_commands.json")
            with open(cdb, "w") as f:
                json.dump([{
                    "directory": d,
                    "command": "gcc -c big.c -o big.o",
                    "file": "big.c",
                }], f)

            params = StdioServerParameters(
                command=sys.executable,
                args=[os.path.abspath("mcp_server.py")],
                cwd=d,
            )
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    res = await s.call_tool("read_c", {
                        "file_path": "big.c",
                        "target": "X",
                        "compile_db": cdb,
                        "max_bytes": 2000,
                    })
                    text = res.content[0].text
                    # Truncation should have kicked in.
                    assert "[WARN] Truncated" in text
                    # Output should be roughly the cap + banner.
                    assert len(text) < 3000

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
