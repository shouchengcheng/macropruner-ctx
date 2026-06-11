# MacroPruner-Ctx — Usage Guide

This is the full operator's manual. The README is the elevator pitch; this document is the workshop.

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

**regex** is the default. It implements a full C preprocessor expression evaluator in pure Python. It understands `defined()`, `&&`/`||`/`!`, `MACRO == N`, `IS_ENABLED()`, hex literals, case-insensitive matching, numeric macro values. Output preserves the original C structure — macros stay as macros, includes stay as includes. **Use this for normal LLM reading.**

**clang** wraps the actual `clang -E` preprocessor. The output is *fully preprocessed* — macros expanded, includes inlined. **This is not what you want the LLM to read** (the structure is gone), but it's the ground truth: if `clang -E` says this code survives when compiled for target X, then the regex backend should agree. **Use it to cross-validate suspicious regex output, never for routine LLM context.**

`auto` mode picks clang when available, regex otherwise. This is what you want for one-shot scripts where setup time matters.

### 1.3 The three compression stages

The pruner applies three reductions in order:

1. **Macro pruning** (always on). Drops `#if MACRO != active_value` blocks.
2. **Skeletonization** (only with `read_c_skeleton`). Strips function bodies, keeps signatures.
3. **Dependency graphing** (only with `read_c_with_deps`). Walks the include tree, target file fully pruned, dependency files pruned + skeletonized. Stage 3 Phase 2: includes inside inactive `#if` blocks are NOT followed.

You can stack stages 1 + 3, but not 2 + 3 (skeletonizing a target that already has skeletonized dependencies is redundant and confusing).

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
.venv/bin/python test_backends.py
.venv/bin/python test_mcp_server.py
```

All 9 suites should print `All tests passed!` or `=== N/N passed ===`.

## 3. Project-level configuration

### 3.1 The `.macroprunerrc` file

Put this in your project root:

```ini
# /home/me/my-firmware/.macroprunerrc

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

# Token budget cap. 0 = unlimited. (Stage 4 feature; currently
# documented but not enforced.)
token_budget      = 0

# Extra -I include directories.
include_dirs      = [third_party/inc, vendor/inc]
```

### 3.2 Search order

When the tool needs `target` or `compile_db`, it searches:

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

## 5. The four tools in detail

### 5.1 `read_c` — the workhorse

```
read_c(
    file_path: str,                # required: relative or absolute path
    target: str = "",              # optional, falls back to .macroprunerrc
    compile_db: str = "",          # optional, falls back to .macroprunerrc
    mode: str = "physical",        # 'physical' or 'virtual'
    backend: str = "regex",        # 'regex' | 'clang' | 'auto'
)
```

**Returns:** A markdown-styled block with a summary header + the pruned code.

```
/* --- MacroPruner-Ctx ---------------------------- */
/* Target:    PRODUCT_3                            */
/* Lines:     187/420 dropped (44.5%)              */
/* Tokens:    1230/2870 saved (42.9%)              */
/* Mode:      physical                             */
/* Backend:   regex                                */
/* ------------------------------------------------ */

[pruned code here]
```

The "Tokens" number is an estimate, not a billing figure. It's `chars / 3.7`, calibrated against cl100k and o200k tokenizers (the ones used by GPT-4 and Claude 3.5). Accuracy: ±15% for code.

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
- Skeletonizes (function bodies stripped)
- Emits a section per file

**Stage 3 Phase 2:** includes inside inactive `#if` blocks are NOT followed. This is the killer feature — without it, the LLM hallucinates about structs defined in headers the target product never compiles.

**When to use:** When the LLM needs to understand cross-file relationships. "What does this struct look like? What other functions are in this module?" Without `with_deps`, the LLM would need a separate `read_c_skeleton` call per header, and would still miss the macro-aware filtering.

**When NOT to use:** For files with deep include chains (Linux kernel, big middleware). Set `max_depth=1` to keep token usage bounded.

### 5.4 `apply_patch` — write back changes

```
apply_patch(
    file_path: str,
    diff: str,                     # unified diff format
)
```

Validates the diff with `git apply --check` (requires the file to be in a git repo), then applies it. Returns a success or error message.

The LLM should generate the diff after reading the pruned file with `read_c`. Patch the **original** file, not the pruned view (the pruned view has dead code removed; the patch references original line numbers).

**When to use:** After the LLM proposes a change and you want to commit it.

**When NOT to use:** When the file isn't in a git repo (fall back to manual editing). When the change is large enough that a full file rewrite makes more sense than a diff.

## 6. Backend selection: when each makes sense

```
┌──────────────────────────────────┬────────────────────┐
│ Situation                        │ Use                │
├──────────────────────────────────┼────────────────────┤
│ Routine code reading             │ regex (default)    │
│ Output looks suspicious         │ clang              │
│ CI gate, no clang available     │ regex (forced)     │
│ Quick debugging, want truth     │ clang              │
│ Cross-checking two implementations│ both, diff them   │
└──────────────────────────────────┴────────────────────┘
```

The `auto` mode picks clang when available. If you want to force a specific backend, pass it explicitly.

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

## 8. Performance and caching

### 8.1 compile_commands.json cache

`CompileDBParser` keeps a process-level cache of parsed compile DBs, keyed by absolute path. The cache invalidates on file mtime change, so editing compile_commands.json in a running agent session picks up the new content on the next call.

Cap: 16 entries (LRU-ish by mtime). For typical projects with one or two build directories, this never evicts.

Disable cache: import `cc_parser` and call `clear_cache()`. The MCP server doesn't expose this — it's for tests and tooling.

### 8.2 Token estimation

Token counts in the output banner use a character-based estimator (`chars / 3.7`) calibrated against cl100k and o200k. Accuracy: ±15% for code. For exact counts, run the output through `tiktoken` or your vendor's SDK yourself.

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

### 9.4 Bulk pruning for offline analysis

The backend module is importable directly:

```python
from backends import get_backend

backend = get_backend("regex")
result = backend.prune("src/main.c", "PRODUCT_3", "build/compile_commands.json")
print(f"Saved {result.reduction_percentage}% of lines")
print(result.code)
```

## 10. Troubleshooting

### 10.1 "no clang binary found on PATH"

`clang` backend is unavailable. Either install clang or pass `backend="regex"` (or `backend="auto"`, which falls back automatically).

### 10.2 Output is unchanged from input

Most common cause: `target` doesn't match any `#ifdef` in the file. Verify by switching to `mode="virtual"` — you should see `[INACTIVE]` markers where the pruning was supposed to happen.

### 10.3 Token count looks wrong

The estimator is calibrated for cl100k/o200k. Older models (GPT-3 davinci, claude-1) tokenize differently; the estimate is less accurate for them. The number is a rough guide, not a billing figure.

### 10.4 compile_commands.json not being picked up

Set `MACROPRUNER_CONFIG=/abs/path/to/.macroprunerrc` and check `default_target` / `compile_db` are correct. Or pass them explicitly to the MCP call.

### 10.5 read_c_with_deps skips a header I expected

Stage 3 Phase 2: that header is inside an inactive `#if` block. Verify with `mode="virtual"` on the root file.

## 11. Reference

### 11.1 MCP tool parameters

| Tool | Required | Optional | Defaults to |
|---|---|---|---|
| `read_c` | `file_path` | `target`, `compile_db`, `mode`, `backend` | `.macroprunerrc` or built-in |
| `read_c_skeleton` | `file_path` | `target`, `compile_db`, `mode` | same |
| `read_c_with_deps` | `file_path` | `target`, `compile_db`, `mode`, `max_depth` | same; `max_depth=2` |
| `apply_patch` | `file_path`, `diff` | — | — |

### 11.2 `.macroprunerrc` keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `default_target` | string | `""` | Default target for the MCP tools |
| `compile_db` | path | `""` | Path to `compile_commands.json` |
| `default_backend` | string | `"regex"` | `regex` / `clang` / `auto` |
| `default_mode` | string | `"physical"` | `physical` / `virtual` |
| `default_max_depth` | int | `2` | `read_c_with_deps` traversal depth |
| `token_budget` | int | `0` | Stage 4 placeholder |
| `include_dirs` | list of strings | `[]` | Extra `-I` paths |
