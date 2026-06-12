# MacroPruner-Ctx — Usage Guide

This is the operator's manual. The [README](../README.md) is the elevator pitch; this document is the workshop.

## 1. Concepts

### 1.1 The problem in one diagram

```
# What LLM sees today (no pruning)
src/main.c (raw, 2000 lines)
├── #if PRODUCT_A — code for product A (800 lines)
├── #else        — code for product B (1200 lines)
└── #endif
Result: LLM reads 2000 lines, hallucinates about both products.

# What LLM sees with MacroPruner-Ctx (target=PRODUCT_A)
src/main.c (pruned, 800 lines)
└── #if PRODUCT_A — code for product A (800 lines)
Result: LLM reads 800 lines, focuses on the product you care about.
```

The compression is "free" in the sense that the LLM never needed to see the inactive code — it was never going to compile for the chosen target.

### 1.2 The two backends

| Backend | Speed | Output | When to use |
|---|---|---|---|
| `regex` | Fast | Original C structure, macros intact | **Default; what LLMs should read** |
| `clang` | Slower | Fully preprocessed (macros expanded, includes inlined) | Ground-truth oracle for cross-validation |

**regex** implements a full C preprocessor expression evaluator in pure Python. It understands `defined()`, `&&`/`||`/`!`, `MACRO == N`, `IS_ENABLED()`, hex literals, case-insensitive matching, numeric macro values. The output preserves the original C structure — macros stay as macros, includes stay as includes. **Use this for normal LLM reading.**

**clang** wraps the actual `clang -E` preprocessor. The output is *fully preprocessed* — macros expanded, includes inlined. **This is not what you want the LLM to read** (the structure is gone), but it's the ground truth: if `clang -E` says this code survives when compiled for target X, then the regex backend should agree. **Use it to cross-validate suspicious regex output, never for routine LLM context.**

`auto` mode picks clang when available, regex otherwise. This is what you want for one-shot scripts where setup time matters.

### 1.3 The three compression stages

The pruner applies three reductions in order:

1. **Macro pruning** (always on, `read_c`). Drops `#if MACRO != active_value` blocks.
2. **Skeletonization** (only with `read_c_skeleton`). Strips function bodies, keeps signatures.
3. **Dependency graphing** (only with `read_c_with_deps`). Walks the include tree, target file fully pruned, dependency files pruned + skeletonized. **Stage 3 Phase 2: includes inside inactive `#if` blocks are NOT followed.**

You can stack stages 1 + 3, but not 2 + 3 (skeletonizing a target that already has skeletonized dependencies is redundant and confusing).

### 1.4 The four sources of truth for the pruner's input

When the LLM calls `read_c("src/main.c")` with no other arguments, the pruner needs to know:

- **What macros are active** → from `compile_commands.json`'s `-D` flags
- **What include paths to follow** → from `-I` in the same file
- **What `#if` conditions to evaluate** → from those active macros
- **What file to read** → from `file_path`

In priority order, the LLM / config / compile_db supply this information:

| Source | Priority | Example |
|---|---|---|
| MCP call argument | Highest | `read_c(file_path="...", target="X")` |
| `.macroprunerrc` | Middle | `default_target = X` in the project root |
| `compile_commands.json` | Lowest | The `-DCHIP_WS63=1 -DPRODUCT_TYPE=3` flags |
| Hard-coded fallback | Last resort | `target="DEFAULT"` placeholder |

---

## 2. Installation

### 2.1 From source

```bash
git clone https://github.com/shouchengcheng/macropruner-ctx.git
cd macropruner-ctx
python3 -m venv .venv
source .venv/bin/activate
pip install mcp
```

The `.venv` is required: MCP SDK needs Python 3.10+, and a venv keeps its dependencies isolated from your system Python.

### 2.2 Verify

```bash
.venv/bin/python test_pruner.py
.venv/bin/python test_pruner_realistic.py
.venv/bin/python test_expr_eval.py
.venv/bin/python test_skeletonizer.py
.venv/bin/python test_dep_graph.py
.venv/bin/python test_conditional_dep_graph.py
.venv/bin/python test_cc_parser_cache.py
.venv/bin/python test_config.py
.venv/bin/python test_errors.py
.venv/bin/python test_token_budget.py
.venv/bin/python test_clang_sysroot.py
.venv/bin/python test_patch_applier.py
.venv/bin/python test_cli.py
.venv/bin/python test_backends.py
.venv/bin/python test_mcp_server.py
```

All 15 suites should print `All tests passed!` or `=== N/N passed ===`.

### 2.3 Optional: clang for the oracle backend

The regex backend works out of the box. For the clang backend (ground-truth oracle):

```bash
# Ubuntu / Debian
sudo apt install clang

# macOS
brew install llvm
```

The backend looks for `clang`, `clang-14`, `clang-13`, etc. on PATH. If none is found, `is_available()` returns False and the backend gracefully falls back to `regex`.

---

## 3. Project-level configuration

### 3.1 The `.macroprunerrc` file

Put this in your project root (NOT the macropruner-ctx directory):

```ini
# .macroprunerrc

# Default target when MCP calls omit the target arg.
default_target    = PRODUCT_3

# Path to compile_commands.json. Relative paths resolve against
# the project root. The tool also auto-discovers:
#   build/compile_commands.json
#   compile_commands.json
compile_db        = build/compile_commands.json

# Which backend to use by default:
#   regex  - pure Python, fast, default
#   clang  - ground truth oracle
#   auto   - clang if available, else regex
default_backend   = regex

# Pruning mode by default:
#   physical - drop inactive lines (most token savings)
#   virtual  - replace with [INACTIVE] markers (preserves line numbers)
default_mode      = physical

# read_c_with_deps traversal depth (1-5).
default_max_depth = 2

# Token budget cap. 0 = unlimited. (Stage 4; enforced in P3-1.)
token_budget      = 0

# Extra -I include directories.
include_dirs      = [third_party/inc, vendor/inc]

# ─── Cross-compile SDK support for the clang backend (P4-1) ───
# Path to your SDK's sysroot. Without this, clang's default
# sysroot has no idea where the SDK's headers live, so
# #include resolution fails on real-world SDKs.
# Required for riscv32-linux-musl, aarch64-linux-gnu, etc.
pruner.sysroot = <cross-sdk-sysroot>

# Optional. Override the --target= value clang sees. If the
# compile_db entry's command already has --target= that wins.
# Otherwise the inherited -march / -mabi from the cdb is used
# (clang 14+ can usually infer a target from those).
pruner.extra_target = riscv32-linux-musl
```

### 3.2 Search order

When the tool needs `target`, `compile_db`, or `sysroot`, it searches:

1. The MCP call's argument (highest priority)
2. `$MACROPRUNER_CONFIG` env var (absolute path to a custom config file)
3. `<project_root>/.macroprunerrc`  (or `<project_root>/macroprunerrc`)
4. `~/.macroprunerrc`
5. Built-in defaults

For most setups, you only need step 3.

### 3.3 Section syntax

`.macroprunerrc` is `KEY = VALUE` with optional `[section]` headers. Bare keys (no section) implicitly belong to `[pruner]`. Unknown sections and unknown keys are kept under `_extra` for forward-compat and ignored.

```ini
# All of these set the same key:
default_target = PRODUCT_A
pruner.default_target = PRODUCT_A
[pruner]
default_target = PRODUCT_A
```

Full reference: [docs/CONFIG.md](CONFIG.md).

---

## 4. MCP integration

### 4.1 Hermes Agent

```bash
hermes mcp add macropruner --command "/abs/path/to/macropruner-ctx/mcp_wrapper.sh"
hermes mcp list             # verify it's registered
hermes mcp test macropruner  # ping the server
```

The wrapper script exists because Hermes's `--command` parameter takes a single executable path, not `python3 /path/to/script.py`. The wrapper invokes the venv's Python and the MCP server.

### 4.2 Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "macropruner": {
      "command": "/abs/path/to/macropruner-ctx/.venv/bin/python3",
      "args": ["/abs/path/to/macropruner-ctx/mcp_server.py"]
    }
  }
}
```

Restart Claude Desktop. The four tools will appear in the tool list.

### 4.3 Other MCP clients

Any client that supports stdio MCP works. Point it at `python3 mcp_server.py` (or the wrapper) and you're done.

---

## 5. The four tools in detail

### 5.1 `read_c` — the workhorse

```
read_c(
    file_path: str,                # required: relative or absolute path
    target: str = "",              # optional, falls back to .macroprunerrc
    compile_db: str = "",          # optional, falls back to .macroprunerrc
    mode: str = "physical",        # 'physical' or 'virtual'
    backend: str = "regex",        # 'regex' | 'clang' | 'auto'
    token_budget: int = 0,         # Stage 4 cap; 0 = no cap
    sysroot: str = "",             # Clang-only: cross-compile SDK sysroot
    extra_target: str = "",        # Clang-only: --target= value
)
```

**Returns:** A markdown-styled block with a summary header + the pruned code.

```
/* --- MacroPruner-Ctx ---------------------------- */
/* Target:    PRODUCT_3                           */
/* Lines:     187/420 dropped (44.5%)              */
/* Tokens:    1230/2870 saved (42.9%)              */
/* Mode:      physical                             */
/* Backend:   regex                                */
/* ------------------------------------------------ */

[pruned code here]
```

The "Tokens" number is an estimate, not a billing figure. It's `chars / 3.7`, calibrated against cl100k and o200k tokenizers (the ones used by GPT-4 and Claude 3.5). Accuracy: ±15% for code.

When `token_budget` is set, the banner adds an extra line:

```
/* Degraded: skeleton                       */   # pruned → skeleton, fits
/* [WARN] Over budget: pruned=1850, skel=711, cap=80 */   # neither fits
```

**When to use:** Anytime you want to look at a single C file with cleanup.

**When NOT to use:** When you only need function signatures (use `read_c_skeleton`). When you need struct definitions from headers (use `read_c_with_deps`).

### 5.2 `read_c_skeleton` — fast module overview

```
read_c_skeleton(
    file_path: str,
    target: str = "",
    compile_db: str = "",
    mode: str = "physical",
)
```

Same arguments as `read_c`, but additionally strips all function bodies. Keeps:
- `#define` / `#include` directives
- `struct` / `union` / `enum` / `typedef` definitions
- Function signatures (return type + name + parameters)

Replaces function bodies with `{ /* ... */ }`.

```
/* --- MacroPruner-Ctx (Skeleton) ----------------- */
/* Target:    PRODUCT_3                           */
/* Original:  1588 lines                            */
/* Skeleton:  63 lines                             */
/* Stripped:  15 functions                         */
/* Backend:   regex                                */
/* ------------------------------------------------ */

#include "uart.h"
... (struct definitions, signatures)
int uart_init(struct uart_ctrl *);
{ /* ... */ }
```

**When to use:** When you're new to a file and want a quick map. Saves 70-90% of tokens vs `read_c` for typical files.

**When NOT to use:** When you need implementation details (use `read_c`).

### 5.3 `read_c_with_deps` — multi-file context

```
read_c_with_deps(
    file_path: str,
    target: str = "",
    compile_db: str = "",
    mode: str = "physical",
    max_depth: int = 2,            # 1-5; how deep to walk the include tree
)
```

Prunes the target file fully, then walks the `#include` tree. For each dependency:
- Resolves the include path using `-I` from compile_db
- Prunes with the same target
- **Skips includes inside inactive `#if` blocks** (Stage 3 Phase 2)
- Skeletonizes (function bodies stripped)
- Emits a section per file

```
/* --- MacroPruner-Ctx (with deps) ------------------ */
/* Target: PRODUCT_3                                */
/* Root: main.c                                      */
/* Dependencies: 3 files                             */
/* Max depth: 2                                      */
/* Mode: physical                                    */
/* Backend: regex                                    */
/* ------------------------------------------------- */

/* ══ TARGET FILE: main.c ═══════════════════════ */
/* Original: 230 lines | Pruned: 95 lines            */

void main(void) { ... }

/* ══ DEPENDENCY: proto.h ═══════════════════════ */
/* Skeleton: 12 lines | Functions stripped: 3       */

typedef struct {...};
int proto_version(void);
```

**When to use:** When the LLM needs to understand cross-file relationships. "What does this struct look like? What other functions are in this module?" Without `with_deps`, the LLM would need a separate `read_c_skeleton` call per header, and would still miss the macro-aware filtering.

**When NOT to use:** For files with deep include chains (Linux kernel, big middleware). Set `max_depth=1` to keep token usage bounded.

### 5.4 `apply_patch` — write back changes

```
apply_patch(
    file_path: str,
    diff: str,                     # unified diff format
)
```

Validates the diff against the original file, applies it, and returns:
- `[OK] Patch applied to <file> via git.` — git fast path (file is in a git repo)
- `[OK] Patch applied to <file> via builtin.` — built-in pure-Python applier (no git needed)
- `[OK] Patch applied to ... [WARN] Syntax check found N issue(s): ...` — patch applied, but the post-apply sanity check (brace balance, #if/#endif balance) flagged something
- `[FATAL] ...` — the diff did not match the current file; the call did not succeed

The LLM should generate the diff after reading the pruned file with `read_c`. Patch the **original** file, not the pruned view (the pruned view has dead code removed; the patch references original line numbers).

**Backend selection:**
- If the file is in a git working tree, `git apply --check` + `git apply` is tried first (the most reliable path)
- Otherwise (or if git rejects the diff), a built-in pure-Python applier kicks in
- The applier does NOT do fuzzy matching; if the diff's `@@ -N,M @@` line offsets don't match the current file, it fails loudly

**When to use:** After the LLM proposes a change and you want to commit it.

**When NOT to use:** When the diff's offsets have drifted (regenerate from current file content first). For non-unified-diff workflows, use git apply / patch directly.

---

## 6. Backend selection: when each makes sense

```
┌──────────────────────────────────┬────────────────────┐
│ Situation                        │ Use                │
├──────────────────────────────────┼────────────────────┤
│ Routine code reading             │ regex (default)    │
│ Output looks suspicious         │ clang              │
│ CI gate, no clang available     │ regex (forced)     │
│ Quick debugging, want truth     │ clang              │
│ Cross-checking implementations │ both, diff them   │
│ Cross-compile SDK (riscv32)     │ regex + sysroot    │
│ Clang oracle + cross-compile    │ clang + --sysroot  │
└──────────────────────────────────┴────────────────────┘
```

The `auto` mode picks clang when available. If you want to force a specific backend, pass it explicitly.

For the cross-compile case, see [docs/BACKENDS.md](BACKENDS.md).

---

## 7. The #if grammar (regex backend)

The regex backend's expression evaluator understands the full subset of C preprocessor expressions that real-world embedded code uses:

| Pattern | Example | Notes |
|---|---|---|
| Bare identifier | `#if DEBUG` | True if `DEBUG` defined |
| `defined()` | `#if defined(WIFI)` | True if `WIFI` defined |
| Bare `defined` | `#if defined WIFI` | Both forms work |
| `&&` / `\|\|` / `!` | `#if A && !B` | Logical operators |
| Parens | `#if (A \|\| B) && C` | Grouping |
| Equality | `#if VERSION == 3` | Numeric compare |
| Inequality | `#if VERSION != 1` | |
| Relational | `#if COUNT > 10` | `<`, `>`, `<=`, `>=` |
| Arithmetic | `#if OFFSET + 1 < MAX` | `+`, `-`, `*`, `/`, `%` |
| Hex | `#if FLAG == 0xFF` | `0x`/`0X` prefix |
| Whitelisted macros | `#if IS_ENABLED(CONFIG_FOO)` | Linux kernel style |
| Case-insensitive | `#if Product_A == 1` | All identifier matching |

Numeric values come from compile_db's `-D` flags. `-DARCH=2` makes `ARCH` evaluate to `2`. `-DFOO` (no value) makes `FOO` evaluate to `1` (defined, no value).

**Deliberate non-features:**

- **Comma operator**, **sizeof**, **ternary `?:`** — uncommon in `#if`; add on request
- **Bitwise `&` `|` `^` `~`** — uncommon in `#if` for embedded; add on request
- **Multi-arg `IS_ENABLED(MACRO, X)`** — only the single-arg form is whitelisted; multi-arg falls through to the parser

If you hit a real codebase that needs one of these, the parser raises `ValueError` and the pruner falls back to treating the block as inactive. No silent wrong answers.

---

## 8. Performance and caching

### 8.1 compile_commands.json cache

`CompileDBParser` keeps a process-level cache of parsed compile DBs, keyed by absolute path. The cache invalidates on file mtime change, so editing compile_commands.json in a running agent session picks up the new content on the next call.

Cap: 16 entries (LRU-ish by mtime). For typical projects with one or two build directories, this never evicts.

Disable cache: `from cc_parser import clear_cache`. The MCP server doesn't expose this — it's for tests and tooling.

### 8.2 Token estimation

Token counts in the output banner use a character-based estimator (`chars / 3.7`) calibrated against cl100k and o200k. Accuracy: ±15% for code. For exact counts, run the output through `tiktoken` or your vendor's SDK yourself.

### 8.3 Per-call latency

| Operation | Time |
|---|---|
| `cli.py read` single file (< 100KB) | 0.1 - 0.2s |
| `cli.py skeleton` single file | 0.1s |
| `cli.py diff` (regex part) | 0.1s |
| MCP `read_c` (stdio roundtrip) | 0.5 - 0.6s |
| 30MB cdb first load (one-time) | < 0.05s (subsequent: cache hit) |
| clang backend (cold, cross-compile SDK) | 0.5s |

MCP stdio roundtrip is dominated by JSON-RPC serialization + Hermes / Claude Desktop scheduling, not by the pruner.

---

## 9. Workflows

### 9.1 Reviewing a firmware change

```
LLM session:
  read_c(file_path="src/wifi.c")
    → gets the active code, 60% fewer tokens
  read_c_with_deps(file_path="src/wifi.c", max_depth=3)
    → gets wifi.c + headers it pulls in
  "Look at this code. What's wrong?"
    → LLM responds with diagnosis
  apply_patch(file_path="src/wifi.c", diff="...")
    → write the LLM's fix back
```

### 9.2 Comparing two products

```
# Same source file, two different targets:
read_c(file_path="src/main.c", target="PRODUCT_3")
read_c(file_path="src/main.c", target="PRODUCT_5")
# diff the two outputs in your head or with `diff` to see what changes
```

### 9.3 Auditing a suspicious regex output

```
read_c(file_path="src/main.c", target="X", backend="regex")
# Looks weird? Cross-check:
read_c(file_path="src/main.c", target="X", backend="clang")
# diff them. The clang output is fully preprocessed so direct
# comparison is hard, but if the regex output contains blocks that
# the clang output doesn't, that's a pruner bug.
```

### 9.4 Cross-compile SDK oracle (P4-1)

```bash
# Tell the regex backend where the cross SDK is via .macroprunerrc:
cat > .macroprunerrc <<'EOF'
default_target = ws63
compile_db     = build/compile_commands.json
pruner.sysroot = <cross-sdk-sysroot>
pruner.extra_target = riscv32-linux-musl
EOF

# Now clang can also do its job on the cross SDK:
.venv/bin/python cli.py read uart.c --backend clang
# or via MCP:
read_c(file_path="src/uart.c", backend="clang")
```

See [docs/BACKENDS.md](BACKENDS.md) for the full cross-compile workflow.

### 9.5 Bulk pruning for offline analysis

The backend module is importable directly:

```python
from backends import get_backend

backend = get_backend("regex")
result = backend.prune("src/main.c", "PRODUCT_3", "build/compile_commands.json")
print(f"Saved {result.reduction_percentage}% of lines")
print(result.code)
```

For batch jobs, pre-warm the cache by calling `_prune_file` once with any project file; subsequent calls skip the cdb parse.

---

## 10. Troubleshooting

### 10.1 "no clang binary found on PATH"

`clang` backend is unavailable. Either install clang (see §2.3) or pass `backend="regex"` (or `backend="auto"`, which falls back automatically).

### 10.2 Output is unchanged from input

Most common cause: `target` doesn't match any `#ifdef` in the file. Verify by switching to `mode="virtual"` — you should see `[INACTIVE]` markers where the pruning was supposed to happen.

### 10.3 Token count looks wrong

The estimator is calibrated for cl100k/o200k. Older models (GPT-3 davinci, claude-1) tokenize differently; the estimate is less accurate for them. The number is a rough guide, not a billing figure.

### 10.4 compile_commands.json not being picked up

Set `MACROPRUNER_CONFIG=/abs/path/to/.macroprunerrc` and check `default_target` / `compile_db` are correct. Or pass them explicitly to the MCP call.

### 10.5 read_c_with_deps skips a header I expected

Stage 3 Phase 2: that header is inside an inactive `#if` block. Verify with `mode="virtual"` on the root file.

### 10.6 clang fails on cross-compile SDK

Pass `sysroot` (CLI `--sysroot`, MCP `sysroot=`, or `.macroprunerrc` `pruner.sysroot`). See [docs/BACKENDS.md](BACKENDS.md).

### 10.7 apply_patch fails with "context mismatch"

The diff's line offsets have drifted from the current file. Regenerate the diff against the current file content. The applier does NOT do fuzzy matching by design — this is a feature, not a bug (silent fuzzy matches are how bugs creep in).

### 10.8 Cache is stale after editing compile_commands.json

`CompileDBParser` invalidates the cache on mtime change. If you suspect a stale cache, call `from cc_parser import clear_cache` (or restart the MCP server).

---

## 11. Reference

### 11.1 MCP tool parameters

| Tool | Required | Optional | Defaults to |
|---|---|---|---|
| `read_c` | `file_path` | `target`, `compile_db`, `mode`, `backend`, `token_budget`, `sysroot`, `extra_target` | `.macroprunerrc` or built-in |
| `read_c_skeleton` | `file_path` | `target`, `compile_db`, `mode` | same |
| `read_c_with_deps` | `file_path` | `target`, `compile_db`, `mode`, `max_depth` | same; `max_depth=2` |
| `apply_patch` | `file_path`, `diff` | — | — |

### 11.2 `.macroprunerrc` keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `pruner.default_target` | string | `""` | Default target for the MCP tools |
| `pruner.compile_db` | path | `""` | Path to `compile_commands.json` |
| `pruner.default_backend` | string | `"regex"` | `regex` / `clang` / `auto` |
| `pruner.default_mode` | string | `"physical"` | `physical` / `virtual` |
| `pruner.default_max_depth` | int | `2` | `read_c_with_deps` traversal depth |
| `pruner.token_budget` | int | `0` | Stage 4 cap (P3-1); 0 = unlimited |
| `pruner.include_dirs` | list of strings | `[]` | Extra `-I` paths |
| `pruner.sysroot` | string | `""` | Clang-only: cross-compile SDK sysroot (P4-1) |
| `pruner.extra_target` | string | `""` | Clang-only: `--target=` value (P4-1) |

---

## 12. The standalone CLI

For users who don't want to spin up an MCP server (CI scripts, ad-hoc inspection, batch jobs in a Makefile):

```bash
# Prune a single file to stdout
python3 cli.py read src/main.c --target PRODUCT_3
# or via -m (if macropruner-ctx is on PYTHONPATH):
python3 -m cli read src/main.c --target PRODUCT_3

# Skeletonize
python3 cli.py skeleton src/main.c --target PRODUCT_3

# Sanity-check: run both regex and clang, compare active-line sets
python3 cli.py diff src/main.c --target PRODUCT_3
# OK: regex and clang agree on all 13 lines being active or inactive.
# or:
# Disagreement: 5 line(s) only regex-active, 0 line(s) only clang-active.
#   regex active, clang inactive: [6, 7, 8, 9, 10]
```

CLI flags (all optional; fall back to `.macroprunerrc`):

| Flag | Meaning |
|---|---|
| `--target NAME` | Target product/macro |
| `--cdb PATH` | Path to `compile_commands.json` |
| `--mode physical\|virtual` | Pruning mode |
| `--backend regex\|clang\|auto` | Backend selection |
| `--sysroot PATH` | Clang-only: cross-compile SDK sysroot |
| `--target-arg ARCH` | Clang-only: `--target=` value |

Exit codes:
- `0` — success (or warnings only)
- `1` — fatal error (file not found, malformed diff, etc.)

`.macroprunerrc` lookup uses the file's directory as project root, not cwd, so `python3 cli.py read /any/path/file.c` will pick up the config from the file's project, not the caller's.

---

## 13. Error handling

All four tools return error information as tagged text rather than raising exceptions. The tags are stable strings the LLM (or your test suite) can grep for:

- `[FATAL]` — the call did not succeed. The user must fix something. Examples: file not found, invalid path, compile_commands.json missing, malformed diff.
- `[ERROR]` — unexpected internal failure. Examples: parser crash, IO error mid-call.
- `[WARN]` — call succeeded with caveats. Examples: one dep file unreadable in `read_c_with_deps`, but the rest came back. A patch applied but the post-apply syntax check flagged a structural issue.

In your LLM prompt you can include a one-liner like:
> When a tool call returns `[FATAL]`, the call did not succeed. Adjust the arguments and try again. `[WARN]` means the call worked but with caveats.

The same convention is used by the CLI on stderr.

Full reference: [docs/ERRORS.md](ERRORS.md).
