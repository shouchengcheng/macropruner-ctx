# MacroPruner-Ctx

> **Macro-aware C/C++ code pruner for LLM agents.** Strips inactive `#ifdef` branches before code reaches the LLM context, cutting token usage 30-70% on real embedded projects.

A Model Context Protocol (MCP) server. Hooks into Hermes, Claude Desktop, or any MCP client. Reads your `compile_commands.json` to know which `#define` flags are active for the target product, then prunes accordingly. Three layers of compression: macro pruning → skeletonization → dependency graph traversal. Two interchangeable backends: a fast pure-Python regex engine and a clang-based ground-truth oracle.

## What it solves

You write one firmware project. Five product variants share the source tree, gated by `#ifdef PRODUCT_A` / `#ifdef PRODUCT_B` / etc. You ask the LLM to review the code for product 3. Standard AI tools dump the entire file — all 5 product branches — into the prompt. The LLM wastes tokens reading code it doesn't care about, and hallucinates field accesses that exist in product 4's struct but not product 3's.

MacroPruner-Ctx sees the same situation the compiler sees: "for product 3, this `#if` block is dead." It strips the dead code, hands the LLM a focused view of the active code, and shows you how much it saved.

## TL;DR

```bash
# 1. Install
git clone https://github.com/shouchengcheng/macropruner-ctx.git
cd macropruner-ctx
python3 -m venv .venv && source .venv/bin/activate
pip install mcp

# 2. (Optional but recommended) Drop a config in your project root
cat > /path/to/your/firmware/.macroprunerrc <<'EOF'
default_target = PRODUCT_3
compile_db      = build/compile_commands.json
default_backend = auto
EOF

# 3. Register with Hermes
hermes mcp add macropruner --command "/path/to/macropruner-ctx/mcp_wrapper.sh"

# 4. Use it from any LLM session
# Agent: read_c(file_path="src/main.c")
#        ^ compiles with target=PRODUCT_3, returns ~50% fewer tokens
```

## Features at a glance

| Capability | Status |
|---|---|
| `#ifdef` / `#ifndef` / `#else` / `#elif` (nested) | ✅ |
| `#if MACRO == N` / `!=` / `<` / `>` / `<=` / `>=` | ✅ |
| `#if defined(A) && defined(B)` (full expression grammar) | ✅ |
| `#if IS_ENABLED(CONFIG_X)` (Linux kernel style, whitelist) | ✅ |
| Case-insensitive identifier matching | ✅ |
| Hex literals (`0xFF`) and numeric macro values (`-DARCH=2`) | ✅ |
| Token budget savings shown in output banner | ✅ |
| `.macroprunerrc` for project-level defaults | ✅ |
| `compile_commands.json` mtime-based cache | ✅ |
| Pluggable backends: regex (fast) + clang (ground truth) | ✅ |
| Conditional `#include` traversal (Phase 2) | ✅ |
| 4 MCP tools: `read_c`, `read_c_skeleton`, `read_c_with_deps`, `apply_patch` | ✅ |
| Standalone CLI: `read` / `skeleton` / `diff` subcommands | ✅ |
| Standalone unified-diff applier (no git required) | ✅ |
| Post-apply C syntax sanity check | ✅ |
| Tagged error strings (`[FATAL]` / `[ERROR]` / `[WARN]`) | ✅ |
| 13 test suites / 178+ cases, all passing | ✅ |

## Architecture

```
LLM Agent (Hermes, Claude Desktop, ...)
       │
       │  MCP protocol (stdio)
       ▼
┌──────────────────────────────────────────────────────────┐
│ mcp_server.py — reads/writes files, dispatches to backs  │
│                                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ read_c      │  │ read_c_skel  │  │ read_c_w_deps  │  │
│  │ (prune)     │  │ (prune+skel) │  │ (multi-file)   │  │
│  └─────┬───────┘  └──────┬───────┘  └────────┬───────┘  │
│        │                  │                    │          │
│  ┌─────▼──────────────────▼────────────────────▼───────┐  │
│  │           PrunerBackend (pluggable)                 │  │
│  │  ┌──────────────┐  ┌──────────────┐                 │  │
│  │  │ regex (fast) │  │ clang (truth)│                 │  │
│  │  └──────┬───────┘  └──────┬───────┘                 │  │
│  └─────────┼─────────────────┼─────────────────────────┘  │
│            │                 │                            │
│  ┌─────────▼─────────┐  ┌────▼────────────────────┐      │
│  │ PrunerCore        │  │ ClangBackend            │      │
│  │  + ExpressionEval │  │  (subprocess: clang -E) │      │
│  └───────────────────┘  └─────────────────────────┘      │
└──────────────────────────────────────────────────────────┘
```

## The 4 MCP tools

### `read_c(file_path, target?, compile_db?, mode?, backend?)`
Prune inactive `#ifdef` blocks from a single C/C++ file. Returns the active code path with a summary header showing lines + tokens saved.

```c
/* --- MacroPruner-Ctx ---------------------------- */
/* Target:    PRODUCT_3                            */
/* Lines:     187/420 dropped (44.5%)              */
/* Tokens:    1230/2870 saved (42.9%)              */
/* Mode:      physical                             */
/* Backend:   regex                                */
/* ------------------------------------------------ */

void init_product3(void) { /* active branch */ }
void init_default(void) { /* fallback */ }
```

### `read_c_skeleton(file_path, target?, compile_db?, mode?)`
Prune, then strip function bodies. Keep `struct`/`enum`/`typedef` definitions and function signatures only. Use this for fast module-overview reads where 70-90% token reduction matters.

### `read_c_with_deps(file_path, target?, compile_db?, mode?, max_depth?)`
Prune the target file, then walk the `#include` tree and skeletonize every dependency. Includes inside inactive `#if` blocks are NOT followed (Stage 3 Phase 2) — no more hallucinated structs from headers the target product never sees.

### `apply_patch(file_path, diff)`
Write a unified diff back to the original file. Use it after `read_c` to commit LLM-suggested changes with minimal blast radius.

## Pluggable backends

| Backend | Speed | Output | Use when |
|---|---|---|---|
| `regex` | Fast | Original C, macros intact | Default; what LLMs should read |
| `clang` | Slower | Fully preprocessed (macros expanded) | Cross-validating regex output |
| `auto` | — | `clang` if available, else `regex` | When you want the best available |

The `clang` backend invokes the actual `clang -E` preprocessor and uses line-marker analysis to map preprocessed code back to the original file's line numbers. It gives you ground-truth: "if you actually compiled target=PRODUCT_3, this is exactly what you'd see." Useful as an oracle when the regex output looks suspicious.

## Configuration

Drop a `.macroprunerrc` in your project root:

```ini
# .macroprunerrc
default_target    = PRODUCT_3                # Falls back to this when MCP calls omit target
compile_db        = build/compile_commands.json   # Path relative to project root
default_backend   = auto                     # 'regex' | 'clang' | 'auto'
default_mode      = physical                 # 'physical' | 'virtual'
default_max_depth = 3                        # For read_c_with_deps
token_budget      = 0                        # 0 = unlimited (Stage 4 placeholder)
include_dirs      = [third_party/inc]        # Extra -I for headers
```

With this file in place, every MCP call in the project no longer needs `target` or `compile_db` arguments.

Search order: `$MACROPRUNER_CONFIG` env var > `<project_root>/.macroprunerrc` > `~/.macroprunerrc` > defaults.

## When to use which tool

```
┌─────────────────────────────────────────────────────────────┐
│  Just want to read a file with cleanup?        → read_c     │
│  Need a quick module overview?                  → skel       │
│  Need cross-file context (struct defs)?         → w_deps     │
│  Ready to write back LLM-suggested changes?     → apply_patch│
│  Output looks weird, want ground truth?         → clang      │
└─────────────────────────────────────────────────────────────┘
```

## What's NOT here (deliberate non-goals)

- **Symbol-level analysis.** A clangd-based indexer handles "which functions does this file call?" better than we ever will. Use clangd.
- **LSP integration.** We're a preprocessor for LLM context, not an editor.
- **Auto-pruning without target.** We always need to know what to keep.
- **Writing back patches that aren't unified diffs.** Use git apply / patch for non-diff workflows.

## Standalone CLI

If you want to prune files without spinning up an MCP server, there's a CLI:

```bash
# Prune a file to stdout
python3 -m macropruner read src/main.c --target PRODUCT_3

# Skeletonize
python3 -m macropruner skeleton src/main.c --target PRODUCT_3

# Diff regex vs clang backends (sanity check)
python3 -m macropruner diff src/main.c --target PRODUCT_3
```

The CLI reads `.macroprunerrc` from the file's directory (not cwd), so it behaves the same way the MCP tools do. Exit codes: 0 = success, 1 = fatal error (file not found, etc.). Warnings stay in stdout without changing the exit code.

## Error handling

Tool return strings are tagged with severity:

- `[FATAL]` — the call did not succeed; check the message and the hint
- `[ERROR]` — unexpected internal failure
- `[WARN]` — call succeeded with caveats (e.g. one dep file in `read_c_with_deps` was unreadable, but the rest came back)

The LLM should treat `[FATAL]` and `[ERROR]` as "this call did not succeed, retry with different args" and `[WARN]` as "this call succeeded but with caveats; you may want to mention it." The tags are stable strings you can grep for in test assertions.

## Install & test

```bash
git clone https://github.com/shouchengcheng/macropruner-ctx.git
cd macropruner-ctx
python3 -m venv .venv && source .venv/bin/activate
pip install mcp

# Run all 13 test suites
for t in test_pruner test_pruner_realistic test_expr_eval test_skeletonizer \
         test_dep_graph test_conditional_dep_graph test_cc_parser_cache \
         test_config test_errors test_patch_applier test_cli test_backends \
         test_mcp_server; do
    .venv/bin/python $t.py
done

# Or use the CLI without an MCP server
.venv/bin/python cli.py read test_samples/test_main.c --target ENABLED_FEATURE
```

## Documentation

- [docs/usage.md](docs/usage.md) — full usage walkthrough with examples
- [INTEGRATION.md](INTEGRATION.md) — agent integration guide (Hermes, Claude Desktop)
- [PLAN.md](PLAN.md) — design decisions, milestone history, roadmap
- [SETUP.md](SETUP.md) — environment setup

## License

[Add your license here.]
