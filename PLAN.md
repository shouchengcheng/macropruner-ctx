# Role & Context
You are an expert Principal Software Engineer specializing in C/C++ compiler toolchains, AST parsing, and LLM orchestration workflows. You are tasked with developing a high-performance productivity tool named **`MacroPruner-Ctx`**.

### The Core Pain Point
In large-scale, multi-firmware C/C++ embedded or Linux systems, a single repository often generates multiple target products. Developers heavily rely on conditional compilation (`#ifdef`, `#ifndef`, `#else`, `#endif`) and preprocessor macros to isolate product-specific code. 
When feeding code files into LLM prompts for analysis, standard AI tools are "macro-blind"—they inject the entire file, including thousands of lines of inactive, uncompiled code from other products. This leads to massive token waste, contextual noise, and severe LLM hallucinations.

### Project Vision
`MacroPruner-Ctx` acts as a **"Macro-Aware Context Pruner"** between a complex C/C++ repository and an LLM. It mimics a compiler's preprocessor to strip out inactive code, extract structural skeletons, and generate optimized, highly-concentrated context prompts for LLMs.

---

# 🏗️ System Architecture & Core Modules

You must maintain and develop the tool across these four discrete functional modules:

1. **Compile DB Parser (`cc_parser.py`)**
   - **Input:** Target source file path (e.g., `src/net/lwip_port.c`) and the project's `compile_commands.json`.
   - **Logic:** Locate the matching file entry. Safely tokenize the `command` or `arguments` array (handling spaces/escapes). Use regex to filter out all global macros defined via `-D` flags (e.g., `-DPRODUCT_TYPE=3`, `-DUSE_LWIP`).
   - **Output:** A dictionary/list of active macros for that specific file.

2. **Conditional Compilation Pruner (`pruner_core.py`)**
   - **Input:** Raw source code text, list of active macros.
   - **Logic:** A robust line-by-line state machine utilizing a stack to handle deeply nested conditional blocks (`#ifdef`, `#ifndef`, `#elif`, `#else`, `#endif`).
   - **Pruning Strategies (Configurable):**
     - *Physical Deletion:* Drop inactive code blocks entirely to minimize tokens.
     - *Virtual Folding:* Replace inactive blocks with `/* [Skipped for TARGET_PROD] */` and pad with empty lines to preserve original file line numbers for accurate debugging.

3. **Context Aggregator (`aggregator.py`)**
   - **Logic:** Reads a local `PROJECT_MANIFEST.md` containing global rules, system architecture, and coding guidelines. Combines the [Global Rules] + [Active Macros Info] + [Pruned Clean Source Code] into a single structured Markdown Prompt.
   - **Token Guard:** Integrates a token counter. If the aggregate prompt exceeds a set budget (e.g., 10k tokens), triggers secondary pruning (e.g., stripping non-essential comments or enabling skeleton mode).

4. **Interface & Delivery Engine (`cli.py` / `main.py`)**
   - **CLI Mode:** Command-line tool supporting flags like `macropruner ./main.c --copy` (autocopy pruned text to clipboard).
   - **LLM Direct Mode:** Optional integration with LLM APIs (e.g., Gemini API) allowing inline queries like: `macropruner ./main.c -q "Explain this timeout logic"`.

---

# 🚀 Engineering Roadmap & Multi-File Escalation Strategy

To scale this tool for massive codebases, we implement a **Four-Stage Code Reduction Funnel**:
- **Stage 1 (Macro Pruning):** Drop inactive conditional code (Reduces size by 30-60%).
- **Stage 2 (Skeletonization):** Strip function bodies `{ ... }` using lightweight AST tokens or regex, keeping only `struct` definitions, `#defines`, and function signatures when full code isn't required.
- **Stage 3 (Dependency Graphing):** Parse `#include` trees and symbols to pull target files as full-text (pruned) while pulling immediate dependencies as structural skeletons only.
- **Stage 4 (Token Budgeting):** Rigid cap enforcement prior to LLM dispatch.

---

# ✅ Current Project Status

## Milestone 1: Complete (legacy)
- CompileDBParser, ConditionalPruner, MCP Server, Test Suite — all shipped.

## Milestone 2: P0 Hardening — Complete

### New Module: `expr_eval.py`
A full-featured C preprocessor expression evaluator. Replaces the original
hand-rolled `defined()`/bare-macro check with a recursive-descent parser
covering the patterns real embedded codebases actually use:

| Pattern                              | Status |
|--------------------------------------|--------|
| `defined(X)` and `defined X`         | ✅      |
| `MACRO == N` / `!=` / `<` / `>` / `<=` / `>=` | ✅ |
| `&&` / `\|\|` / `!` / parens         | ✅      |
| `MACRO + N` / `* N` / `- N` (arithmetic) | ✅  |
| Hex literals (`0xFF`)                | ✅      |
| `IS_ENABLED(CONFIG_X)` (whitelist)   | ✅      |
| `IS_BUILTIN(CONFIG_X)` (whitelist)   | ✅      |
| Case-insensitive identifier matching | ✅      |
| Numeric macro values (`-DARCH=2`)    | ✅      |

Test coverage: **28 cases** (`test_expr_eval.py`).

### New Module: `backends/` — Pluggable Pruner Backends

Two backends are shipped and registered automatically:

| Backend  | Speed  | Output                                | Use case                                 |
|----------|--------|---------------------------------------|------------------------------------------|
| `regex`  | Fast   | Original C structure, macros intact   | Default; what LLMs should read           |
| `clang`  | Slow   | Fully preprocessed (macros expanded)  | Ground-truth oracle / cross-validation   |

Auto mode (`backend='auto'`) picks `clang` if available, else falls back
to `regex`. Backends are stateless — no global cache, per-call
instantiation only.

`clang` backend implementation:
- Locates `clang` (or `clang-14/13/12/11/10`) on PATH
- Runs `clang -E -w` with `compile_commands.json`'s `-D`/`-I`
- Walks the line-marker stream to identify the original-file lines
  that survived preprocessing (= active). Skipped ranges are emitted
  as `(start, end)` tuples in `PruneResult.skipped_ranges`
- Skipped/inactive original lines are reconstructed as `(start, end)`
  ranges for the caller

### Stage 3 Phase 2 — Conditional-Aware Include Traversal

`DependencyGraph` now exposes `conditional_build()` in addition to
`build()`. The conditional variant:

- Tracks a stack of `#if`-block active/inactive states while walking
  the file
- Evaluates every `#if`/`#ifdef`/`#ifndef`/`#elif` with the same
  `ExpressionEvaluator` the pruner uses
- Follows an `#include` ONLY if all enclosing `#if` blocks are active
- Records skipped includes as `header.h [skipped]` in the adjacency
  list (so callers can see what was considered, even if not followed)
- Returns `(graph, active_includes_set)` for downstream consumers

`read_c_with_deps` has been upgraded to use `conditional_build()`. When
you ask for `target=PRODUCT_A` and the file has

```c
#ifdef PRODUCT_A
#include "product_a.h"
#else
#include "product_b.h"
#endif
```

…the dependency walker follows only `product_a.h`, not `product_b.h`.

### Bug Fixes
- **elif-chain semantics**: the previous `_handle_elif` / `_handle_else`
  used a "previous branch was inactive" heuristic, which let `#else`
  fire incorrectly when an earlier `#if` was active. Replaced with an
  explicit `taken` flag on `ConditionalBlock`. Verified by
  `test_pruner_realistic.py`.
- **`#if` directive not handled**: `process_line` previously only
  dispatched `#ifdef` / `#ifndef`. Bare `#if` fell through and was
  emitted as-is. Now routed through `_handle_if` → `evaluate_condition`.
- **Unbalanced code raises instead of warns**: `prune()` now appends
  a warning comment for unclosed conditionals (matches the
  `unbalanced_real_world` test contract).

### Total Test Coverage

| Module                  | Tests |
|-------------------------|-------|
| `pruner_core.py`        | 12    |
| `expr_eval.py`          | 28    |
| `skeletonizer.py`       | 9     |
| `dep_graph.py` (uncond) | 9     |
| `dep_graph.py` (cond)   | 7     |
| `backends/`             | 8     |
| `mcp_server.py` E2E     | 6     |
| `pruner_realistic.py`   | 10    |
| **Total**               | **89** |

### MCP API Changes (additive only)

`read_c` and `read_c_with_deps` accept an optional `backend` parameter:
- `regex` (default)
- `clang` (ground truth)
- `auto`

The header banner now includes the active backend name, so callers can
verify which backend produced the result.

### Next Steps (not started)
- **Stage 4 (Token Budgeting):** Token counter + auto-skeletonization
  when budget exceeded.
- **Cross-validation CLI:** `macropruner --diff regex clang <file>` to
  show where the two backends disagree.
- **Editor / LSP integration:** Pre-filter C/C++ buffers before sending
  to LSP.
- **Stage 4 (Token Budgeting):** Rigid cap enforcement before LLM dispatch.
