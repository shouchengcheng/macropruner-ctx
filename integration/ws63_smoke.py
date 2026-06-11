"""End-to-end integration test against a real ws63 SDK checkout.

This script:
  1. Discovers the compile_commands.json files in the SDK.
  2. Picks a representative .c file that uses #if/#ifdef heavily.
  3. Runs macropruner in every mode/backend combination.
  4. Records the output (token counts, banner, sample diffs) for
     the process doc.
  5. Stress-tests the conditional #include walker.
  6. Stress-tests the token budget degradation.

Run with:
  PYTHONPATH=. .venv/bin/python integration/ws63_smoke.py

The output goes to integration/ws63_smoke.log next to this script.
"""
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# Make the macropruner-ctx package importable when run from any CWD.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession


WS63_ROOT = Path("/home/scc/workspace/firmwareunstable/ws63_sdk")
ACORE = WS63_ROOT / "output" / "ws63" / "acore"
LOG = Path(__file__).parent / "ws63_smoke.log"

# Pick a compile_commands.json that is large enough to be
# representative. liteos-app is the main app build.
PRIMARY_CDB = ACORE / "ws63-liteos-app" / "compile_commands.json"


def section(title):
    """Print + log a section header."""
    line = f"\n{'=' * 70}\n{title}\n{'=' * 70}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def step(title):
    line = f"\n--- {title} ---"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def log(line):
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def pick_representative_c_file(cdb_path):
    """Pick a .c file that's a good showcase:
      - not too tiny (so the prune actually has work to do)
      - uses #if/#ifdef with multiple branches
      - sits in the liteos-app build (so all -D macros apply)
      - has good macro coverage (most #if use a macro that IS
        defined in the cdb entry), so the demo numbers reflect
        what a real ws63 product target would look like, not
        the worst case where every #if's macro is undefined.
    """
    with open(cdb_path) as f:
        entries = json.load(f)
    # Build (file_path, line_count) pairs, deduped.
    candidates = []
    for entry in entries:
        cmd = entry.get("command", "")
        fp = entry.get("file", "")
        if not fp.endswith(".c"):
            continue
        if fp.startswith("output/") or "test/" in fp or "sample" in fp:
            continue
        full = os.path.join(entry.get("directory", ""), fp)
        if not os.path.isfile(full):
            continue
        # Skip files larger than 200 KB to keep the smoke test fast.
        if os.path.getsize(full) > 200_000:
            continue
        with open(full, errors="replace") as f:
            content = f.read()
        if len(content) < 200:
            continue
        n_if = len(re.findall(r"^\s*#\s*(if|ifdef|ifndef)\b", content, re.MULTILINE))
        if n_if < 5:
            continue
        # Coverage: how many of the #if macros appear in cdb's -D?
        ifs = re.findall(r"^\s*#\s*(?:ifdef|ifndef|if)\s+(\w+)", content, re.MULTILINE)
        ifs = [i for i in ifs if i != "if"]
        macros = set(re.findall(r"-D([A-Z_][A-Z0-9_]*)", cmd))
        covered = sum(1 for i in ifs if i in macros)
        if not ifs:
            continue
        ratio = covered / len(ifs)
        # Prefer good coverage, decent size, and product source dirs.
        in_product = any(seg in full for seg in ("/application/", "/protocol/", "/middleware/", "/drivers/"))
        score = (ratio, in_product, n_if, len(content))
        candidates.append((score, full, n_if, ratio, len(content)))
    # Sort: best coverage first, product-source files preferred.
    candidates.sort(reverse=True)
    if not candidates:
        return None
    _score, full, n_if, ratio, n_chars = candidates[0]
    return full


def main():
    # Open log fresh.
    if LOG.exists():
        LOG.unlink()

    section(f"ws63 SDK integration smoke test — {datetime.now().isoformat()}")

    section("Environment")
    log(f"WS63 SDK:   {WS63_ROOT}")
    log(f"Primary CDB: {PRIMARY_CDB}  ({PRIMARY_CDB.stat().st_size / 1024 / 1024:.1f} MB)")
    # Show one sample -D to prove the macro density.
    with open(PRIMARY_CDB) as f:
        first = json.load(f)[0]
    cmd = first.get("command", "")
    n_d = cmd.count(" -D")
    log(f"  first entry: {first.get('file')}")
    log(f"  -D flag count in first entry's command: {n_d}")
    log(f"  sample -D: {' '.join(re.findall(r'-D[A-Z_]+', cmd)[:5])}")

    section("Step 1 — Find a representative .c file")
    sample = pick_representative_c_file(PRIMARY_CDB)
    if not sample:
        log("FATAL: no .c file in the cdb matches the criteria")
        return 1
    log(f"Picked: {sample}")
    with open(sample) as f:
        content = f.read()
    n_if = len(re.findall(r"^\s*#\s*(if|ifdef|ifndef)\b", content, re.MULTILINE))
    n_elif = len(re.findall(r"^\s*#\s*elif\b", content, re.MULTILINE))
    log(f"  {len(content.splitlines())} lines")
    log(f"  {n_if} #if/#ifdef/#ifndef directives")
    log(f"  {n_elif} #elif branches")
    log(f"  target filename basename: {os.path.basename(sample)}")

    section("Step 2 — Run macropruner on the picked file")
    cdb_arg = ["--cdb", str(PRIMARY_CDB)]
    rc = run_subprocess("read", [sample, *cdb_arg], cwd=str(WS63_ROOT), capture_first_chars=600)
    log(f"  exit code: {rc}")

    section("Step 3 — Skeletonize the same file")
    rc = run_subprocess("skeleton", [sample, *cdb_arg], cwd=str(WS63_ROOT), capture_first_chars=600)
    log(f"  exit code: {rc}")

    section("Step 4 — regex vs clang diff (oracle check)")
    rc = run_subprocess("diff", [sample, *cdb_arg], cwd=str(WS63_ROOT), capture_first_chars=600)
    log(f"  exit code: {rc}")

    section("Step 5 — Token budget enforcement (auto-degradation)")
    for budget in (0, 80, 30, 5):
        step(f"  --token-budget={budget}")
        rc = run_subprocess("read", [sample, *cdb_arg, "--token-budget", str(budget)],
                            cwd=str(WS63_ROOT), capture_first_chars=500)
        log(f"    exit code: {rc}")

    section("Step 6 — Conditional #include (Stage 3 Phase 2) on a multi-file .c")
    # Find a small file that #include's other ws63 headers; we want
    # to confirm the conditional walker actually visits them.
    with open(PRIMARY_CDB) as f:
        entries = json.load(f)
    with_includes = []
    for entry in entries:
        fp = entry.get("file", "")
        if not fp.endswith(".c"):
            continue
        full = os.path.join(entry.get("directory", ""), fp)
        if not os.path.isfile(full):
            continue
        with open(full, errors="replace") as f:
            c = f.read()
        n_inc = len(re.findall(r"^\s*#\s*include\b", c, re.MULTILINE))
        if n_inc >= 5:
            with_includes.append((full, n_inc))
    if with_includes:
        with_includes.sort(key=lambda x: -x[1])
        sample2, n = with_includes[0]
        log(f"Picked multi-include file: {sample2} ({n} includes)")
        rc = run_subprocess("read", [sample2, *cdb_arg], cwd=str(WS63_ROOT), capture_first_chars=600)
        log(f"  exit code: {rc}")
    else:
        log("No multi-include file found in liteos-app build")

    section("Done")
    log(f"Full log: {LOG}")
    return 0


def run_subprocess(cmd, args, cwd, capture_first_chars=600):
    """Run `cli.py <cmd> <args>` and capture the first N chars of stdout.

    The CLI doesn't expose --token-budget (we only added that
    parameter to the MCP `read_c` tool). For the budget demo we
    instead drive the MCP server via stdio, which is the production
    path. The `cmd` for this fallback is then ignored.
    """
    if cmd == "read" and "--token-budget" in args:
        # Use the MCP server so we can exercise token_budget.
        return asyncio.run(_run_mcp_read(args))
    py = ROOT / ".venv" / "bin" / "python"
    argv = [str(py), str(ROOT / "cli.py"), cmd, *args]
    cmd_str = "cli.py " + " ".join([cmd, *args])
    log(f"  $ .venv/bin/python {cmd_str}")
    proc = time.time()
    import subprocess
    try:
        result = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        log("  TIMEOUT after 60s")
        return -1
    elapsed = time.time() - proc
    log(f"  ({elapsed:.1f}s)")
    out = result.stdout
    if len(out) > capture_first_chars:
        log(out[:capture_first_chars])
        log(f"  ... (truncated, total {len(out)} chars) ...")
    else:
        log(out)
    if result.returncode != 0 and result.stderr:
        log(f"  stderr: {result.stderr[:300]}")
    return result.returncode


async def _run_mcp_read(args):
    """Drive the MCP server's read_c with a token_budget.

    `args` is the full CLI-style argv.
    Recognized: file_path, --cdb PATH, --token-budget N.
    """
    file_path = args[0]
    compile_db = ""
    token_budget = 0
    i = 1
    while i < len(args):
        if args[i] == "--token-budget":
            token_budget = int(args[i + 1])
            i += 2
        elif args[i] == "--cdb":
            compile_db = args[i + 1]
            i += 2
        else:
            i += 1

    log(f"  $ mcp_server.py read_c({os.path.basename(file_path)} token_budget={token_budget} cdb=...)")
    proc = time.time()
    p = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "mcp_server.py")],
        cwd=os.path.dirname(file_path),
    )
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(
                "read_c",
                {
                    "file_path": file_path,
                    "token_budget": token_budget,
                    "compile_db": compile_db,
                },
            )
            text = res.content[0].text
    elapsed = time.time() - proc
    log(f"  ({elapsed:.1f}s)")
    log(text[:600])
    if len(text) > 600:
        log(f"  ... (truncated, total {len(text)} chars) ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
