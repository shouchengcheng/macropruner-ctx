# MacroPruner-Ctx

> **Macro-aware C/C++ code pruner for LLM agents.** Strips inactive `#ifdef` branches before code reaches the LLM context, cutting token usage **7% – 87%** on real embedded projects.

A Model Context Protocol (MCP) server with a standalone CLI fallback. Hooks into Hermes, Claude Desktop, or any MCP client. Reads your `compile_commands.json` to know which `#define` flags are active for the target product, then prunes accordingly.

Three compression layers (macro prune → skeletonize → cross-file graph), two interchangeable backends (regex for speed, clang for ground truth), one shared `.macroprunerrc` config so the LLM never has to repeat the same flags.

---

## What's in v0.5

| Capability | Status |
|---|---|
| `#ifdef` / `#ifndef` / `#else` / `#elif` (nested) | ✅ |
| Full `#if` expression grammar: `defined()`, `&&`/`\|\|`/`!`, `MACRO == N`, arithmetic, hex | ✅ |
| `#if IS_ENABLED(CONFIG_X)` (Linux kernel style, whitelist) | ✅ |
| Case-insensitive identifier matching | ✅ |
| Token-budget savings shown in the output banner | ✅ |
| Pluggable backends: `regex` (default) + `clang` (ground truth) | ✅ |
| Conditional `#include` traversal — won't pull headers from inactive target branches | ✅ |
| Standalone CLI (`read` / `skeleton` / `diff` subcommands) | ✅ |
| 4 MCP tools: `read_c`, `read_c_skeleton`, `read_c_with_deps`, `apply_patch` | ✅ |
| `.macroprunerrc` for project-level defaults | ✅ |
| `compile_commands.json` mtime-based cache | ✅ |
| Token-budget enforcement with auto-degradation to skeleton | ✅ |
| **Cross-compile SDK support (clang backend)**: `--sysroot` / `--target-arg` for riscv32-linux-musl, aarch64, etc. | ✅ (P4-1) |
| Standalone unified-diff applier (no git required) | ✅ |
| Post-apply C syntax sanity check | ✅ |
| Tagged error strings (`[FATAL]` / `[ERROR]` / `[WARN]`) | ✅ |
| 15 test suites / 196+ cases, all passing | ✅ |

---

## TL;DR — 30 seconds to first run

```bash
# 1. Install
git clone https://github.com/shouchengcheng/macropruner-ctx.git
cd macropruner-ctx
python3 -m venv .venv && source .venv/bin/activate
pip install mcp

# 2. (Recommended) Drop a config in your project root
cat > /path/to/your/firmware/.macroprunerrc <<'EOF'
default_target = PRODUCT_3
compile_db      = build/compile_commands.json
default_backend = auto
EOF

# 3. Register with Hermes
hermes mcp add macropruner --command "/path/to/macropruner-ctx/mcp_wrapper.sh"

# 4. Use it from any LLM session. The agent now just calls:
#    read_c(file_path="src/main.c")
#    ^ config supplies target + compile_db automatically.
#    60%+ tokens gone, no hallucinated macros.
```

Cross-compile SDK users (riscv32, aarch64, etc.), add this to the same `.macroprunerrc`:

```ini
pruner.sysroot      = <cross-sdk-sysroot>
pruner.extra_target = riscv32-linux-musl
```

Then the clang backend can also be used as a ground-truth oracle (see [docs/BACKENDS.md](docs/BACKENDS.md)).

---

## The 4 MCP tools

| Tool | What it does | When to use |
|------|--------------|-------------|
| `read_c` | Prune inactive `#if` blocks. Output preserves original C structure. | Default; what LLMs should read. |
| `read_c_skeleton` | Prune + strip function bodies. Keep `struct`/`enum`/`typedef`/signatures. | Quick module overview. ~70-90% smaller. |
| `read_c_with_deps` | Prune target, walk `#include` tree, skeletonize deps. **Conditional-aware** (won't pull inactive target's headers). | Cross-file context without hallucinated structs. |
| `apply_patch` | Write a unified diff back to the original file. | LLM proposed a change; commit it. Git optional. |

All four accept (in this priority order):

1. Per-call MCP arguments (override everything)
2. `.macroprunerrc` defaults
3. Auto-detected from `compile_commands.json`

Full parameter reference: see [docs/usage.md § 5](docs/usage.md).

---

## Standalone CLI

If you don't want to spin up an MCP server, the CLI covers the same three core operations:

```bash
python3 cli.py read src/main.c --target PRODUCT_3 --cdb build/compile_commands.json
python3 cli.py skeleton src/main.c --target PRODUCT_3
python3 cli.py diff src/main.c --target PRODUCT_3    # regex vs clang oracle
```

The CLI reads `.macroprunerrc` from the file's directory (not cwd) and exits 0 on success, 1 on fatal error. See [docs/usage.md § 12](docs/usage.md) for the full reference.

---

## Architecture

```
LLM Agent (Hermes, Claude Desktop, ...)
       │
       │  MCP protocol (stdio)
       ▼
┌──────────────────────────────────────────────────────────────────┐
│ mcp_server.py — exposes the 4 tools, also runnable as CLI         │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐            │
│  │ read_c      │  │ read_c_skel  │  │ read_c_w_deps  │            │
│  │ (prune)     │  │ (prune+skel) │  │ (multi-file)   │            │
│  └──────┬──────┘  └──────┬───────┘  └────────┬───────┘            │
│         │                │                   │                    │
│  ┌──────▼────────────────▼───────────────────▼───────────────┐   │
│  │              Pluggable PrunerBackend (ABC)               │   │
│  │  ┌──────────────┐  ┌──────────────────────────────┐       │   │
│  │  │ regex (fast) │  │ clang (ground truth oracle) │       │   │
│  │  └──────┬───────┘  └────────┬─────────────────────┘       │   │
│  └─────────┼─────────────────┼─────────────────────────────┘   │
│            │                 │                                  │
│  ┌─────────▼──────┐  ┌───────▼─────────────────────┐             │
│  │ PrunerCore     │  │ ClangBackend                  │             │
│  │ + ExprEval     │  │ (subprocess: clang -E,        │             │
│  │ (recursive-    │  │  + line-marker analysis,      │             │
│  │  descent)      │  │  + compile_db flag inheritance)│            │
│  └────────────────┘  └─────────────────────────────┘             │
└──────────────────────────────────────────────────────────────────┘
```

Layered compression (you pick which layer via the tool name or the CLI subcommand):

1. **Macro prune** (`read_c`) — `#if MACRO != N` blocks deleted
2. **Skeletonize** (`read_c_skeleton`) — function bodies stripped
3. **Dependency graph** (`read_c_with_deps`) — multi-file, conditional-aware

For deeper details see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Real-world numbers

End-to-end test against a real HiSilicon WS63 firmware SDK (riscv32-linux-musl cross-compile, 30 MB `compile_commands.json`, 120+ `-D` macros per file):

| Scenario | Line reduction | Token savings | Time |
|---|---|---|---|
| Single `read_c` on a well-covered middleware file | 20% | 7% | 0.2s |
| Single `read_c` on a 108-include hmac config | 36% | 26% | 0.2s |
| `read_c_skeleton` (function bodies stripped) | 89% | ~80% | 0.1s |
| 30MB cdb loaded once, subsequent calls (mtime cache hit) | — | — | <0.05s |
| MCP stdio roundtrip per call (Hermes / Claude Desktop) | — | — | ~0.5s |
| clang backend with cross-compile SDK + sysroot | n/a | n/a | 0.5s |

Full report: [integration/ws63_integration_report.md](integration/ws63_integration_report.md).

**Token savings range is 7% – 87%** depending on the file. The low end is a well-covered middleware file where most `#if` blocks are active; the high end is a driver file where most `#if` blocks are inactive.

---

## Choose the right backend

| Situation | Use |
|---|---|
| Routine code reading | `regex` (default) |
| Output looks suspicious | `clang` oracle |
| Cross-compile SDK (riscv32, aarch64) | `regex`; `clang` only if you set `pruner.sysroot` |
| Need exact token count | `regex` (estimate is ±15%) |
| CI / batch scripts | CLI; pick backend explicitly per call |

Decision tree and trade-offs: [docs/BACKENDS.md](docs/BACKENDS.md).

---

## Document map

| Doc | What's in it |
|---|---|
| [README.md](README.md) | This file. The 30-second tour. |
| [INTEGRATION.md](INTEGRATION.md) | 中文集成指南。Hermes / Claude Desktop step-by-step. |
| [SETUP.md](SETUP.md) | Environment setup, venv creation, dependency list. |
| [docs/usage.md](docs/usage.md) | Operator's manual. Every tool, every parameter, every workflow. |
| [docs/CONFIG.md](docs/CONFIG.md) | Complete `.macroprunerrc` reference. |
| [docs/BACKENDS.md](docs/BACKENDS.md) | Backend selection, oracle workflow, cross-compile setup. |
| [docs/ERRORS.md](docs/ERRORS.md) | The `[FATAL]` / `[ERROR]` / `[WARN]` error protocol. |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Internal architecture — modules, data flow, extension points. |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Version history (P0-P4). |
| [PLAN.md](PLAN.md) | High-level architecture + milestone history. |
| [demo/README.md](demo/README.md) | Screencast-ready walkthrough (`bash demo/demo.sh`). |
| [integration/ws63_integration_report.md](integration/ws63_integration_report.md) | Real SDK validation report. |

---

## Install & test

```bash
git clone https://github.com/shouchengcheng/macropruner-ctx.git
cd macropruner-ctx
python3 -m venv .venv && source .venv/bin/activate
pip install mcp

# Run all 15 test suites
for t in test_pruner test_pruner_realistic test_expr_eval test_skeletonizer \
         test_dep_graph test_conditional_dep_graph test_cc_parser_cache \
         test_config test_errors test_token_budget test_clang_sysroot \
         test_patch_applier test_cli test_backends test_mcp_server; do
    .venv/bin/python $t.py
done

# Or use the CLI without an MCP server:
.venv/bin/python cli.py read test_samples/test_main.c --target ENABLED_FEATURE

# Or run the end-to-end demo:
bash demo/demo.sh
```

Requires Python 3.10+. No system dependencies except an optional `clang` (for the oracle backend).

---

## What this tool is NOT

- **Not a symbol-level indexer.** clangd does that better. We work at the file level.
- **Not an LSP.** We're a preprocessor for LLM context, not an editor.
- **Not a fuzzy-diff patch applier.** `apply_patch` requires exact line offsets. If your diff has drifted, regenerate from current file content.
- **Not a cross-compile SDK emulator.** We don't ship toolchains. We tell clang where to find yours.

---

## License

[Add your license here.]
