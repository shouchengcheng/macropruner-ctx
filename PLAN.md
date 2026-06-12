# MacroPruner-Ctx — Architecture & Milestone History

> **One-page architecture overview** for engineers reviewing the codebase or planning extensions. The full operator's manual is in [`docs/usage.md`](docs/usage.md).

---

## What it does, in one paragraph

`MacroPruner-Ctx` is an MCP server (with a standalone CLI fallback) that reads C/C++ source files, prunes inactive `#ifdef` branches based on the project's `compile_commands.json`, and hands the LLM a focused view of the active code. It cuts token usage 7%–87% on real embedded projects, depending on the file.

---

## Architecture at a glance

```
                    LLM Agent (Hermes, Claude Desktop, ...)
                                  │
                                  │  MCP protocol over stdio
                                  ▼
┌───────────────────────────────────────────────────────────────────────┐
│ mcp_server.py — registers the 4 tools                                │
│                                                                       │
│  ┌─────────────────┐  ┌──────────────────┐  ┌──────────────────┐      │
│  │ read_c          │  │ read_c_skeleton   │  │ read_c_with_deps │      │
│  │ (prune)         │  │ (prune+skeleton)  │  │ (multi-file)     │      │
│  └────────┬────────┘  └─────────┬────────┘  └─────────┬────────┘      │
│           │                     │                     │              │
│  ┌────────▼─────────────────────▼─────────────────────▼──────────┐   │
│  │                  Pluggable PrunerBackend (ABC)               │   │
│  │  ┌──────────────────┐  ┌────────────────────────────────┐    │   │
│  │  │ regex (default)  │  │ clang (ground truth oracle)   │    │   │
│  │  └────────┬─────────┘  └────────────┬───────────────────┘    │   │
│  └───────────┼─────────────────────────┼────────────────────────┘   │
│              │                         │                            │
│  ┌───────────▼──────────┐    ┌─────────▼──────────────────────┐     │
│  │ PrunerCore           │    │ ClangBackend                    │     │
│  │  + ExpressionEval    │    │  - subprocess: clang -E       │     │
│  │  (recursive-descent  │    │  - compile_db flag inheritance │     │
│  │   parser, ~400 LOC)  │    │  - line-marker analysis         │     │
│  │                      │    │  - sysroot / --target support  │     │
│  └──────────────────────┘    └────────────────────────────────┘     │
└───────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                  PruneResult { code, skipped_ranges,
                                original_code, backend_name,
                                token_estimate, effective_target,
                                effective_compile_db, extra metadata }
```

The three compression stages (callable as different tool names):

1. **Macro prune** (`read_c`) — drop inactive `#if` blocks
2. **Skeletonize** (`read_c_skeleton`) — strip function bodies, keep signatures
3. **Dependency graph** (`read_c_with_deps`) — multi-file, conditional-aware (`#include` inside inactive `#if` is NOT followed)

You can stack 1+3 but not 2+3 (skeletonizing a target that already has skeletonized dependencies is redundant).

---

## Module map

| Module | Lines | Purpose |
|---|---|---|
| `pruner_core.py` | 312 | Stack-based state machine for `#if`/`#ifdef`/`#else`/`#endif`. Holds `ConditionalBlock.taken` flag for elif-chain correctness. Tracks `skipped_ranges` (P2 fix to a long-standing dead-code field). |
| `expr_eval.py` | 423 | Recursive-descent C preprocessor expression evaluator. `defined()`/`defined X`, `&&`/`\|\|`/`!`, `MACRO == N` / `!=` / `<` / `>` / `<=` / `>=`, arithmetic, hex, `IS_ENABLED()` whitelist, case-insensitive, numeric macro values. Raises `ValueError` on malformed input. |
| `cc_parser.py` | 209 | Parses `compile_commands.json`. Extracts per-file `-D` macros and `-I` include dirs. Has process-level mtime cache (P1-3). New `get_entry_tokens_for_file()` method (P4-1) hands the full token list to backends. |
| `skeletonizer.py` | 188 | Strips function bodies, keeps struct/enum/typedef definitions, `#define`/`#include` directives, and function signatures. |
| `dep_graph.py` | 361 | `#include` tree walker. `build()` is unconditional; `conditional_build()` (P0-6) tracks `#if` active state and skips includes inside inactive blocks. |
| `token_counter.py` | 123 | LLM token estimator. `char_estimate` (chars/3.7) + `word_estimate` (subword-corrected). |
| `backends/base.py` | 196 | `PruneResult` dataclass with `token_estimate` property. `PrunerBackend` ABC. `get_backend(name, **kwargs)` factory with auto-registration. |
| `backends/regex_backend.py` | 78 | Wraps `PrunerCore` + `cc_parser` for the fast pure-Python path. |
| `backends/clang_backend.py` | 333 | Wraps `clang -E` for ground-truth. `_filter_tokens_for_clang()` (P4-1) sanitizes the project's gcc command. `get_entry_tokens_for_file()` provides the inherit-point. Auto-detects `--target=` and `--sysroot=`; honors user-supplied overrides. |
| `mcp_server.py` | 519 | MCP server. 4 tools. `_prune_file()` core helper. `_enforce_budget()` (P3-1) for token-budget enforcement. Pass `sysroot`/`extra_target` to clang. |
| `cli.py` | 240 | Standalone CLI. 3 subcommands (`read`/`skeleton`/`diff`). Reads `.macroprunerrc` from file's directory. |
| `config.py` | 220 | `.macroprunerrc` parser. KEY=VALUE syntax with `[sections]`. Bare keys implicitly belong to `[pruner]`. `resolve_compile_db()` walks project. |
| `errors.py` | 100 | `MacroPrunerError` hierarchy. `FatalError` / `TransientError` with formatted() rendering. `format_error()` maps stdlib exceptions to tagged output. `with_fallback()` for per-dep error isolation. |
| `patch_applier.py` | 320 | Standalone unified-diff applier (no git required). Multi-hunk with cumulative net-change offset tracking. `check_c_syntax()` post-apply validator (brace balance, #if/#endif balance, orphan #else). |

---

## Data flow (one `read_c` call)

```
1. Agent invokes read_c(file_path="src/main.c", target="X", compile_db="...").
2. _prune_file() reads .macroprunerrc (if file_path or compile_db is empty).
3. get_backend("regex") → RegexBackend().prune()
4. CompileDBParser(compile_db).extract_macros(file_path)  →  {X: None, ...}
5. PrunerCore(active_macros, mode=physical).prune(source)
   - For each line: parse directive OR push to output if active
   - Track skipped_ranges as (start, end) pairs
6. PruneResult { code, original_code, skipped_ranges, ... }
7. _prune_file() optionally enforces token_budget (P3-1):
   - pruned_tokens > budget → try skeleton
   - if even skeleton > budget → tag as exceeded
8. mcp_server.read_c() formats banner + result.code → return string
9. Banner shows: target, lines dropped, tokens saved, mode, backend,
   optional [Degraded: skeleton] or [WARN] Over budget line
```

The whole pipeline: ~0.2s for typical files, ~0.5s for MCP stdio roundtrip.

---

## Cross-cutting concerns

### Configuration

`.macroprunerrc` is the single source of truth for project-level defaults. Search order: `$MACROPRUNER_CONFIG` > `<project>/.macroprunerrc` > `~/.macroprunerrc` > built-in defaults. See [`docs/CONFIG.md`](docs/CONFIG.md).

### Caching

`CompileDBParser` keeps a process-level mtime cache (16 entries, LRU-by-mtime). `clear_cache()` for tests. The MCP server never exposes the cache; it's an internal perf detail.

### Error handling

Every tool wraps its body in a try/except that converts to tagged text:
- `[FATAL]` — call did not succeed; LLM should retry with different args
- `[ERROR]` — unexpected internal failure
- `[WARN]` — call succeeded with caveats (e.g. one dep file unreadable)

Stable string prefixes the LLM (or your test suite) can grep. See [`docs/ERRORS.md`](docs/ERRORS.md).

### Backend selection

Two backends ship in-tree, both auto-registered on import:
- `regex` (default, fast, pure-Python)
- `clang` (ground truth, requires `clang` binary on PATH; cross-compile SDKs need a user-supplied `sysroot`)

`auto` mode picks clang when available, regex otherwise. The MCP tool can be told to use either explicitly. See [`docs/BACKENDS.md`](docs/BACKENDS.md).

---

## Extension points

If you want to extend macropruner-ctx, here are the natural hook points:

| Want to ... | Touch |
|---|---|
| Add a new `#if` expression operator (e.g. ternary) | `expr_eval.py` — add a token type + a parser method |
| Add a new backend (e.g. cppfront tree-sitter) | `backends/<name>_backend.py` — implement `PrunerBackend`, decorate with `@register_backend` |
| Change how the MCP banner looks | `mcp_server.py` `read_c()` — the f-string block in the `try` body |
| Add a new MCP tool | `mcp_server.py` — `@server.tool(name=..., description=...)` |
| Change how config keys are typed | `config.py` `_coerce()` |
| Add a new error severity | `errors.py` — extend the `MacroPrunerError` hierarchy |
| Add a new CLI subcommand | `cli.py` — `sub.add_parser(...)` + a `_your_subcommand()` function |
| Tune token-budget degradation | `mcp_server.py` `_enforce_budget()` |
| Add a clang flag filter | `backends/clang_backend.py` `_CLANG_FLAG_ALLOWLIST_*` constants |
| Add a new file pattern to auto-discover compile_db | `config.py` `resolve_compile_db()` |

---

## Milestone history

| Milestone | Date | What shipped | Tests |
|---|---|---|---|
| **M1 (legacy)** | pre-project | `CompileDBParser`, `PrunerCore`, MCP server with 1 tool (`read_c`) | 12 |
| **P0 hardening** | this project | Full `#if` expression evaluator (was bare `defined()` only). Pluggable backends (`regex` + `clang`). Conditional `#include` traversal. Bug fixes: elif-chain `taken` flag, bare `#if` dispatch, unbalanced warning. | 12 → 89 |
| **P1 polish** | this project | Token counter (chars/3.7 estimator). `.macroprunerrc` config (KEY=VALUE, sections, coercion). `compile_commands.json` mtime cache. Full README / usage docs rewrite. | 89 → 122 |
| **P2 engineering** | this project | Tagged error protocol (`[FATAL]`/`[WARN]`). Standalone unified-diff applier (no git required). Post-apply C syntax check. Standalone CLI with 3 subcommands. `cli.py diff` regex-vs-clang oracle. | 122 → 178 |
| **P3 hard limits** | this project | Token-budget enforcement with auto-degradation to skeleton. End-to-end screencast-ready demo. | 178 → 184 |
| **P4-1 cross-compile** | this project | `clang` backend now inherits project's `compile_db` flags (--target, -march, -mabi, etc.). Accepts user-supplied `sysroot` for cross-compile SDKs. Verified against mock riscv32-linux-musl sysroot. | 184 → 196 |

Total: **15 test suites / 196+ cases / 5500+ lines of production code**, all passing.

---

## Future work (not started)

- **PyPI publish:** `pip install macropruner-ctx` — package up the current `cli.py`-and-friends for distribution.
- **CI:** GitHub Actions matrix testing on Python 3.10 / 3.11 / 3.12 + clang available / not available.
- **Editor / LSP integration:** Pre-filter C/C++ buffers before sending to LSP. Would slot in as a clangd plugin or a vim/zed plugin.
- **Real C parser for syntax check:** Currently `check_c_syntax()` is brace-counting + ifdef-counting. A pycparser-based check would catch real semantic errors.
- **clang --target from cdb auto-infer:** Currently the user has to pass `pruner.extra_target` if their cdb doesn't have `--target=`. We could infer `riscv32-*` from `-march=rv32*` automatically.
- **Streaming response:** For very large `read_c_with_deps` results, stream the response in chunks instead of buffering the full string.

---

## File index (one-line per file)

| File | Purpose |
|---|---|
| `pruner_core.py` | `#if` state machine |
| `expr_eval.py` | `#if` expression evaluator |
| `cc_parser.py` | `compile_commands.json` parser + cache |
| `skeletonizer.py` | function-body stripper |
| `dep_graph.py` | `#include` walker (with conditional variant) |
| `token_counter.py` | LLM token estimator |
| `errors.py` | error class hierarchy + tagging |
| `patch_applier.py` | standalone diff applier + syntax check |
| `config.py` | `.macroprunerrc` parser |
| `backends/__init__.py` | re-exports + side-effect imports (register) |
| `backends/base.py` | `PruneResult`, `PrunerBackend` ABC, factory |
| `backends/regex_backend.py` | fast pure-Python backend |
| `backends/clang_backend.py` | ground-truth oracle backend |
| `mcp_server.py` | MCP server, the 4 tools |
| `cli.py` | standalone CLI |
| `tests/test_*.py` (×18) | unit + integration tests |
| `examples/README.md` | real-SDK integration template (drop-in) |
| `INTEGRATION.md` | Chinese agent-integration guide |
| `docs/usage.md` | operator's manual |
| `docs/CONFIG.md` | `.macroprunerrc` reference |
| `docs/BACKENDS.md` | backend selection + cross-compile |
| `docs/ERRORS.md` | error protocol |
| `docs/ARCHITECTURE.md` | internal architecture (this file) |
| `docs/CHANGELOG.md` | version history |
| `demo/README.md` | screencast demo guide |
| `README.md` | top-level project page |
| `INTEGRATION.md` | Chinese agent-integration guide |
| `SETUP.md` | environment setup |
| `PLAN.md` | this file |
