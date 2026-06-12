# Backends — Selection Guide

macropruner-ctx ships with two interchangeable backends and one
implicit default. This document explains when to use each, how
`auto` mode works, and how to use the clang backend with
cross-compile SDKs (P4-1).

## The two backends

| Backend | Speed | Output | When to use |
|---|---|---|---|
| `regex` | Fast | Original C structure, macros intact | **Default; what LLMs should read** |
| `clang` | Slower | Fully preprocessed (macros expanded, includes inlined) | Ground-truth oracle; cross-validation; pathological `#if` |
| `auto` | — | `clang` if available, else `regex` | One-shot scripts where setup time matters |

## What the regex backend does

Pure-Python. Implements a full C preprocessor expression evaluator
(`expr_eval.py`) and a stack-based state machine
(`pruner_core.py`). The output preserves the original C structure
— macros stay as macros, includes stay as includes. **This is what
LLMs should read.**

Speed: ~0.1-0.2s for typical files. First call on a new compile DB
pays a ~50ms parse cost; subsequent calls (mtime cache hit) are
<5ms.

Reliability: depends on the evaluator correctly handling the
patterns in the codebase. As of v0.5, it handles:

- `defined(X)` and `defined X`
- `MACRO == N` / `!=` / `<` / `>` / `<=` / `>=`
- `&&` / `||` / `!` / parens
- `MACRO + N` / `* N` / `- N` / `/ N` / `% N`
- Hex literals (`0xFF`, `0XFF`)
- `IS_ENABLED(CONFIG_X)`, `IS_BUILTIN(CONFIG_X)` (whitelisted)
- Case-insensitive identifier matching
- Numeric macro values (`-DARCH=2`)
- Imbalanced `#if` (warns, doesn't crash)

What it does NOT do:

- Comma operator, `sizeof`, ternary `?:` in `#if`
- Bitwise `&` / `|` / `^` / `~` in `#if` (uncommon in real code)
- Multi-arg `IS_ENABLED(X, Y)` (only single-arg whitelisted)

If a `#if` expression is too exotic, the evaluator raises
`ValueError` and the pruner falls back to treating the block as
inactive. No silent wrong answers.

## What the clang backend does

Wraps `clang -E` (the actual preprocessor) as a subprocess.
Output is fully preprocessed: macros expanded, includes inlined,
comments stripped. **This is not what an LLM should read** — the
structure is gone, function bodies are inlined, types are resolved.

It's the **ground truth**. If `clang -E` says this code survives
when compiled for target X, the regex backend should agree.

Speed: ~0.5s per call (subprocess overhead dominates). Cross-compile
SDKs add a bit more for flag parsing.

How it knows which file to preprocess:
1. Looks up the source file in the project's `compile_db` entry
2. Reads the entry's full token list (`command` or `arguments`)
3. Filters those tokens through `_filter_tokens_for_clang()` — keeps
   `--target`, `--sysroot`, `-march`, `-mabi`, `-isystem`, `-D`, `-I`,
   `-W`, `-std=`, etc.; drops `-Wl`, `-Wa`, `-f` (catch-all), `-Werror`,
   and other gcc-specific flags
4. Adds the project macros from `-D` flags
5. Adds the project's `-I` include dirs
6. Runs `clang -E -w` on the file
7. Parses `# N "file"` line markers to identify which original-file
   lines survived preprocessing (= active). Lines marked active form
   the `PruneResult.skipped_ranges` complement.

## Auto mode

`auto` mode picks clang if available, regex otherwise. The check
is per-call: a new `ClangBackend()` instance is created, its
`is_available()` is consulted, and if False the call falls through
to `RegexBackend()`.

When you should use `auto`:

- One-shot scripts (`python3 cli.py read foo.c --backend auto`)
- Cross-project tools that don't know in advance whether the host
  has clang
- Anywhere you want "best available" semantics

When you should NOT use `auto`:

- CI gates that need deterministic behavior (a CI server today
  might have clang, tomorrow might not — you'd rather pin to
  `regex` for reproducibility)
- Latency-sensitive code paths (`auto` does an `is_available()`
  probe that takes a few microseconds; in a tight loop that adds up)

## The cross-compile SDK case (P4-1)

Before P4-1, the clang backend was effectively useless against
cross-compile SDKs (HiSilicon WS63, aarch64, anything with a custom
sysroot). The reason: clang's default sysroot has no idea where
the SDK's cross-compile headers live, so `#include` resolution
fails.

P4-1 fixes this in three ways:

1. **Inheriting compile_db flags.** Instead of throwing away
   the project's `compile_db` entry's command and starting from
   scratch, we read the full token list, filter it through
   `_filter_tokens_for_clang()` (which keeps the cross-compile
   flags clang needs: `--target`, `--sysroot`, `-march`, `-mabi`,
   `-isystem`, `-I`, etc., and drops the ones it doesn't:
   `-Wl,`, `-Wa,`, `-f*` catch-all, `-Werror`, etc.), and use
   those as the base of our clang invocation.

2. **Auto-detecting `--target=` and `--sysroot=`.** If the
   inherited flag list contains `--target=riscv32-linux-musl` or
   `--sysroot=<cross-sdk-sysroot>`, we use those. Otherwise we rely
   on clang's defaults (which works for native builds, fails for
   cross-compile).

3. **User-supplied overrides.** The user can pass `--sysroot`
   (CLI / MCP) or `pruner.sysroot` (`.macroprunerrc`) to force
   clang to use a specific sysroot. Same for `--target-arg` /
   `pruner.extra_target`.

### Cross-compile SDK oracle workflow

Suppose you're working with the HiSilicon WS63 SDK at
`<cross-sdk-install>/`, with a sysroot at `<cross-sdk-sysroot>/`. You
want the clang oracle to actually work on it.

**Step 1: Find the sysroot**

```bash
# ws63 SDK's sysroot is typically:
ls <cross-sdk-sysroot>/

# The compile_db is at:
ls <firmware-project>/output/ws63/acore/ws63-liteos-app/compile_commands.json
```

**Step 2: Write the config**

```ini
# <firmware-project>/.macroprunerrc
default_target    = ws63
compile_db        = output/ws63/acore/ws63-liteos-app/compile_commands.json
default_backend   = regex              # regex always works cross-SDK
default_mode      = physical
default_max_depth = 3

# For the clang oracle:
pruner.sysroot      = <cross-sdk-sysroot>
pruner.extra_target = riscv32-linux-musl
```

**Step 3: Use it**

```bash
# Via the CLI:
.venv/bin/python cli.py read src/uart.c --backend clang

# Or via MCP (LLM agent):
read_c(file_path="<firmware-project>/src/uart.c", backend="clang")
```

**Expected behavior:**

```
/* --- MacroPruner-Ctx (Clang Backend / Oracle) --- */
/* Target:    ws63                                  */
/* Backend:   sysroot=<cross-sdk-sysroot>         */
/* Note: Fully preprocessed by clang -E.             */
/* Macros expanded, #include'd content inlined.       */
/* ...                                              */
```

If you see `[FATAL] RuntimeError: clang -E failed (...) 'port/header.h' file not found`,
the sysroot path is wrong. Run `clang -E -v` on a known file in
the SDK to find where clang is actually looking.

### When the compile_db already has the right flags

The ws63 SDK's compile_db has `-march=rv32imfc -mabi=ilp32f` but
no `--target=`. The clang backend will:

1. Inherit `-march=rv32imfc` from the cdb (kept by filter)
2. Try to use clang's default `--target` (which is the host's
   architecture, NOT riscv32)
3. Fail with `unknown target CPU 'rv32imfc'`

The fix: pass `--target-arg=riscv32` (or `riscv32-linux-musl` for
full glibc/musl target triplet) so clang knows what architecture
to compile for. The `pruner.extra_target` field in
`.macroprunerrc` is the right way to set this for the project.

```ini
# .macroprunerrc for ws63
pruner.extra_target = riscv32-linux-musl
```

### Cross-checking the two backends

Once you have a working clang oracle, the `cli.py diff` subcommand
becomes your CI smoke-test:

```bash
.venv/bin/python cli.py diff src/uart.c
# OK: regex and clang agree on all 13 lines being active or inactive.
# or, on disagreement:
# Disagreement: 5 line(s) only regex-active, 0 line(s) only clang-active.
#   regex active, clang inactive: [6, 7, 8, 9, 10]
```

If the two backends disagree, **trust clang** — it's running the
actual preprocessor. The regex backend has a bug if its active
set is a strict superset of clang's. (In practice, the most common
disagreement is line-1 markers: clang's `# 1 "uart.c"` line marker
may not appear in the output if the include chain pulls all the
source through, leading to a few lines that regex considers active
but clang doesn't tag.)

## Decision tree

```
Do you have clang installed?
├── No  → regex (only option)
└── Yes
    │
    Is the codebase a cross-compile SDK?
    ├── No
    │   │
    │   Do you want ground-truth validation?
    │   ├── No  → regex (default)
    │   └── Yes → use clang as oracle, regex for normal reading
    │            set default_backend=auto in .macroprunerrc
    │
    └── Yes (riscv32-linux-musl, aarch64, ...)
        │
        Have you configured pruner.sysroot + pruner.extra_target?
        ├── No  → regex (only option, clang will fail on cross SDK)
        │         set default_backend=regex
        └── Yes → set default_backend=auto, use clang for oracle
                  set default_backend=clang for specific cross-check calls
```

## Backend compatibility matrix

| Project type | regex | clang | Notes |
|---|---|---|---|
| Native Linux C | ✅ | ✅ | Both work |
| Native macOS C/C++ | ✅ | ✅ | Install clang via `brew install llvm` |
| Cross-compile SDK, no sysroot config | ✅ | ❌ | regex only |
| Cross-compile SDK, with sysroot config | ✅ | ✅ | Both work; clang is the oracle |
| C++ project | ✅ | ✅ | Pass `-x c++` (auto-detected from extension) |
| Kernel / driver code with exotic gcc flags | ✅ | ⚠️ | Filter drops most gcc flags; if some slip through, the error message will name them |
| Code with `_Pragma` / `_Static_assert` etc. | ✅ | ✅ | Preprocessor-only; doesn't care about post-preprocess syntax |
