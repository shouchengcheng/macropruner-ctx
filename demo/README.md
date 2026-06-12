# MacroPruner-Ctx end-to-end demo

A self-contained, scripted demo that walks through the most important
features of macropruner-ctx in eight steps. Designed for screencasts:
each step pauses briefly (configurable) so the viewer can read the
output before the next step scrolls past.

## Quick start

```bash
# Run from the macropruner-ctx root:
bash demo/demo.sh

# Faster run, no pauses (good for CI):
bash demo/demo.sh --no-pause

# Record output to a file (for screencast logs / commit-able transcripts):
bash demo/demo.sh --record /tmp/demo_output.log
```

The script creates a fresh `mktemp -d` working directory under `/tmp`
and tears it down on exit, so re-running doesn't pollute state. To
keep the demo directory around (for inspection), comment out the
`trap "rm -rf $DEMO_DIR" EXIT` line near the top of `demo.sh`.

## What you'll see

| Step | What it shows | Approx time |
|------|---------------|-------------|
| 1 | Set up a sample C project with `#if` chaos | < 1s |
| 2 | `read_c` prunes inactive blocks (~65% reduction) | 1s |
| 3 | `token_budget` auto-degradation when budget is tight | 1s |
| 4 | `read_c_skeleton` strips function bodies | 1s |
| 5 | `read_c_with_deps` cross-file context (Stage 3 Phase 2) | 1s |
| 6 | `apply_patch` on a non-git project (built-in applier) | 1s |
| 7 | `cli.py` standalone (no MCP server required) | 1s |
| 8 | Tagged `[FATAL]` / `[WARN]` error handling | 1s |

Total: ~10s with pauses, ~3s with `--no-pause`.

## Sample project created in Step 1

The demo synthesizes a realistic multi-product C file with the
patterns macropruner was built for:

- `#if PRODUCT_TYPE == N` / `#elif` / `#else` chains
- `#if defined(A) && defined(B)` compound conditions
- `#ifdef DEBUG` blocks
- Conditional `#include "proto.h"` (exercised in Step 5)

The `.macroprunerrc` it drops says `default_target = PRODUCT_3`, so all
the MCP calls in subsequent steps get that target without the LLM
having to pass it explicitly.

## What this demo is NOT

- **Not a real product.** The sample `.c` is contrived to showcase
  every feature in one screen. For real-world numbers on a cross-
  compile SDK, see [`examples/README.md`](../examples/README.md)
  and `docs/BACKENDS.md` § "Cross-compile SDK oracle workflow".
- **Not a test suite.** The demo has no assertions; it's purely
  visual. The actual test suite is in `tests/test_*.py` and verified
  by the CI workflow (`.github/workflows/tests.yml`).
- **Not a benchmark.** The script uses `head -10` on Step 7 to keep
  the screen short. Real cross-compile SDK numbers (7% - 87% token
  savings depending on the file) are documented in
  `docs/BACKENDS.md` and the integration template at
  [`examples/README.md`](../examples/README.md).
- **Not a cross-compile SDK demo.** The sample project uses no SDK
  toolchain. For a real cross-SDK oracle workflow, see
  `docs/BACKENDS.md` § "Cross-compile SDK oracle workflow".

## Customizing

Want a longer pause for recording? Set `PAUSE` env var:

```bash
PAUSE=3 bash demo/demo.sh
```

The `pause()` function uses `$PAUSE` (default 1.5s).

Want to demo a different file? Edit the `cat > "$DEMO_DIR/main.c"`
block in Step 1 and re-run.

Want to demo cross-compile? Set environment variables before running:

```bash
MACROPRUNER_DEMO_SYSROOT=<cross-sdk-sysroot> \
MACROPRUNER_DEMO_TARGET=riscv32-linux-musl \
    bash demo/demo.sh
```

(The demo doesn't yet wire these through to Step 5; pull request
welcome if you want to add it.)

## Embedding in your own product docs

The `--record` flag writes a timestamped log to the path you
specify. Many teams use the demo's output as the canonical "how to
use this tool" walkthrough in their internal wiki:

```bash
bash demo/demo.sh --record docs/macropruner_walkthrough.log
# Then commit docs/macropruner_walkthrough.log alongside docs/.
```

## Related demos

- [`examples/README.md`](../examples/README.md) — drop-in template
  for running macropruner-ctx against a real cross-compile SDK
  (HiSilicon WS63, aarch64, in-house firmware SDKs, etc.). Includes
  `.macroprunerrc` shape, file-picking heuristic, and the
  `clang -E` sysroot flag pattern for riscv32 / aarch64.

- `/tmp/sysroot_demo.py` — build a mock riscv32-linux-musl sysroot,
  drive the clang backend through it. Demonstrates P4-1.
