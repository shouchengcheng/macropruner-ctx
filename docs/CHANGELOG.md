# Changelog

All notable changes to macropruner-ctx, by release.

The format is roughly [Keep a Changelog](https://keepachangelog.com/).
Versions are tagged chronologically. The project is at **v0.5** as of
the latest commit.

## v0.5 — 2026-06-12

The "production-ready" release. After four engineering rounds (P0-P4)
on top of the original MVP, the tool now has all the capabilities
needed to deliver real value to embedded engineering teams.

### Added

- **P0-2 Full `#if` expression evaluator** (`expr_eval.py`).
  The old regex matched only `defined()` and bare macro names.
  v0.5 handles `MACRO == N`, `&&`/`||`/`!`, arithmetic, hex,
  `IS_ENABLED()` whitelisting, case-insensitive matching, numeric
  macro values. 28 unit tests.
- **P0-4 Pluggable clang backend** (`backends/clang_backend.py`).
  Wraps `clang -E` as a subprocess. Walks `# N "file"` line markers
  to map preprocessed output back to original-line active sets.
- **P0-6 Conditional `#include` traversal** (`dep_graph.conditional_build`).
  Walks the include tree, evaluates each enclosing `#if`, follows
  the include only if all enclosing blocks are active. Skipped
  includes are recorded as `header.h [skipped]` in the adjacency
  list.
- **P1-1 Token counter** (`token_counter.py`). Lightweight
  char/3.7 estimator + word-based alternative. `PruneResult.token_estimate`
  surfaces before/after counts. Banner now shows `Tokens: 1230/2870 saved`.
- **P1-2 `.macroprunerrc` config** (`config.py`). `KEY = VALUE` with
  `[sections]`, bare keys implicitly in `[pruner]`. Coercion
  handles bool/int/float/list/quoted. Search order: env var >
  project rc > home rc > defaults.
- **P1-3 compile_commands.json mtime cache** (`cc_parser.py`).
  16-entry cache, LRU-by-mtime. The 100th `read_c` call no longer
  re-parses compile_commands.json. Edits to the cdb in a running
  session are picked up on the next call.
- **P2-1 Tagged error protocol** (`errors.py`). `MacroPrunerError`
  hierarchy. `format_error()` maps stdlib exceptions. `[FATAL]`,
  `[ERROR]`, `[WARN]` stable string prefixes the LLM can grep.
- **P2-2 Standalone unified-diff applier** (`patch_applier.py`).
  No git required. Multi-hunk with cumulative net-change offset
  tracking. `check_c_syntax()` post-apply validator (brace balance,
  `#if`/`#endif` balance, orphan `#else`).
- **P2-3 Standalone CLI** (`cli.py`). `read` / `skeleton` / `diff`
  subcommands. Reads `.macroprunerrc` from the file's directory,
  not cwd. Exits 0 on success, 1 on fatal error.
- **P3-1 Token budget enforcement** (`mcp_server._enforce_budget`).
  Auto-degrade: `pruned → skeleton → [WARN]`. Banner shows
  `Degraded: skeleton` or `[WARN] Over budget: ...`. `.macroprunerrc`
  `pruner.token_budget` supplies the default.
- **P4-1 Cross-compile SDK support**. The clang backend now
  inherits the project's `compile_db` entry's flags (after
  filtering through `_filter_tokens_for_clang` which keeps
  `--target`/`--sysroot`/`-march`/`-mabi`/`-isystem` etc., and
  drops gcc-specific flags like `-f*` catch-all). User can supply
  `sysroot` and `extra_target` via CLI / MCP / `.macroprunerrc`.
  Verified end-to-end against a mock riscv32-linux-musl sysroot.
- **`docs/usage.md`** — 477-line operator's manual. Concepts,
  install, config, MCP integration, every tool's full parameter
  reference, every `#if` grammar supported, performance notes,
  troubleshooting, reference.
- **`docs/CONFIG.md`** — full `.macroprunerrc` reference. All keys,
  value coercion, resolution examples, common mistakes.
- **`docs/BACKENDS.md`** — backend selection guide, decision tree,
  cross-compile SDK workflow, compatibility matrix.
- **`docs/ERRORS.md`** — error protocol reference. All tags, formats,
  per-tool examples, custom error subclass usage.
- **`docs/ARCHITECTURE.md`** — internal architecture deep dive. Data
  flow, module dependency graph, PruneResult lifecycle, preprocessor
  engine, backend implementations, caching, configuration, patch
  layer, MCP integration, extension points, timing breakdown.
- **`integration/ws63_smoke.py`** — automated smoke test against a
  real HiSilicon WS63 firmware SDK.
- **`integration/ws63_integration_report.md`** — 429-line process
  document for the ws63 SDK integration. Real numbers (7% - 87%
  token savings), discovered limits (clang backend cross-compile
  before P4-1), recommendations.
- **`demo/demo.sh`** — 8-step end-to-end demo for screencasts.

### Changed

- **Banner format upgraded.** Now includes target / lines / tokens
  / mode / backend (and optionally `Degraded:` or `[WARN]`
  budget line).
- **read_c and friends** now have target/compile_db as **optional
  arguments** (P1-2: fall back to `.macroprunerrc`).
- **PruneResult** carries `effective_target` and `effective_compile_db`
  so the banner shows the post-fallback values, not the empty
  defaults the caller passed in.
- **`apply_patch`** tries git fast-path first, falls back to
  built-in applier. Runs a post-apply syntax check. Returns
  `[OK] / [FATAL] / [WARN]` tagged output.
- **`#if` directive is now actually handled** (P0 bug fix:
  `process_line` previously only dispatched `#ifdef`/`#ifndef`).
- **elif-chain semantics fixed** (P0 bug fix: replaced
  "previous branch inactive" heuristic with explicit `taken` flag).
- **Unbalanced conditionals** no longer raise; they emit a warning
  comment in the output.

### Fixed

- `skipped_ranges` field on `PruneResult` was dead code (P2 fix:
  PrunerCore now actually populates it).
- `set -e` in demo scripts: `cli.py diff` exits non-zero on
  disagreement, which was killing the demo. Switched to `set +e`.

### Test coverage

- **15 test suites / 196+ cases**, all passing.
- New: `test_expr_eval.py` (28), `test_skeletonizer.py` (9),
  `test_dep_graph.py` (9), `test_conditional_dep_graph.py` (7),
  `test_cc_parser_cache.py` (5), `test_config.py` (10),
  `test_errors.py` (10), `test_token_budget.py` (6),
  `test_clang_sysroot.py` (12), `test_patch_applier.py` (18),
  `test_cli.py` (7), `test_backends.py` (8).
- Updated: `test_pruner.py` (12) and `test_pruner_realistic.py` (10)
  cover realistic multi-product and well-covered C files.

## v0.1 — initial MVP (pre-project)

The first commit of macropruner-ctx. Had:
- `cc_parser.py` (compile_commands.json parser, no cache)
- `pruner_core.py` (state machine, but only `defined()` and bare
  macro names — no full `#if` expression support)
- `skeletonizer.py`
- `dep_graph.py` (unconditional `build()` only)
- `mcp_server.py` (one tool: `read_c` with no `backend` parameter,
  no token budget, no `.macroprunerrc`, no error tagging)
- `test_pruner.py` (7 cases)
- `test_mcp_server.py` (3 cases)

The MVP worked for the simplest case but couldn't handle:
- `#if PRODUCT_TYPE == 3` (returned the whole file unchanged)
- `#if defined(A) && defined(B)` (same)
- `#if IS_ENABLED(CONFIG_X)` (same)
- Case-insensitive macro names
- Cross-compile SDKs (clang would fail on the headers)
- Multi-file context (no conditional include awareness)
- Token counting or budget enforcement
- Tagged error output

The 7 cases in `test_pruner.py` are still in v0.5 and still pass.
