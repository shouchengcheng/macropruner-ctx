# Internal Architecture

Deep dive into how macropruner-ctx works under the hood. If you're
planning to extend it, start here. If you just want to use it,
[docs/usage.md](usage.md) is enough.

## High-level data flow

```
LLM agent
    │
    │  MCP call: read_c(file_path="X", target="Y", compile_db="Z")
    ▼
mcp_server.read_c()
    │
    │ 1. _prune_file() loads .macroprunerrc if file_path or compile_db empty
    │
    │ 2. get_backend("regex", sysroot=S, extra_target=T)
    │      │
    │      │  factory in backends/base.py; resolves the registered name to a class
    ▼
RegexBackend.prune()
    │
    │ 3. compile_db = CompileDBParser(cdb)
    │    .extract_macros(file_path)  →  {"X": None, "Y": None, ...}
    │
    │ 4. PrunerCore(active_macros, mode="physical")
    │    .prune(source)  →  (code, skipped_ranges)
    │
    ▼
PruneResult { code, original_code, skipped_ranges, ... }
    │
    │ 5. mcp_server._enforce_budget() (if token_budget set)
    │    auto-degrade to skeleton if over budget
    │
    │ 6. read_c() formats the banner, returns
    │
    ▼
LLM agent receives:
    /* --- MacroPruner-Ctx ---------------------------- */
    /* Target:    PRODUCT_3                           */
    /* Lines:     14/21 dropped (66.67%)              */
    /* Tokens:    77/118 saved (65.25%)               */
    /* Mode:      physical                            */
    /* Backend:   regex                               */
    /* ------------------------------------------------ */
    
    [pruned code]
```

## Module dependencies

```
mcp_server.py  ──┬──> backends/
                │     │
                │     ├── base.py (PruneResult, PrunerBackend ABC, get_backend factory)
                │     ├── regex_backend.py (wraps PrunerCore)
                │     │     │
                │     │     └──> pruner_core.py
                │     │              │
                │     │              └──> expr_eval.py
                │     │
                │     └── clang_backend.py (subprocess + line-marker parsing)
                │           │
                │           └──> cc_parser.py
                │
                ├──> pruner_core.py  ──> expr_eval.py
                │
                ├──> skeletonizer.py
                │
                ├──> dep_graph.py    ──> expr_eval.py (for conditional include walker)
                │
                ├──> cc_parser.py    ──> token_counter.py (via PruneResult.token_estimate)
                │
                ├──> config.py        (.macroprunerrc parser)
                │
                ├──> errors.py        (FatalError / TransientError / format_error)
                │
                ├──> patch_applier.py (used by apply_patch tool)
                │
                └──> token_counter.py (PruneResult.token_estimate)

cli.py  ──┬──> backends/regex_backend.py (same PruneResult as MCP)
          ├──> skeletonizer.py
          └──> config.py

backends/__init__.py  ──> side-effect imports to trigger @register_backend
```

## PruneResult lifecycle

`PruneResult` is the central data structure. Created by every
backend; consumed by every tool and the CLI.

```python
@dataclass
class PruneResult:
    code: str                                    # the pruned code (or preprocessed for clang)
    skipped_ranges: List[Tuple[int, int]]        # (start_line, end_line) of contiguous inactive
    original_lines: int                          # line count of input file
    pruned_lines: int                            # non-empty line count of `code`
    backend_name: str = "regex"                  # 'regex' or 'clang'
    original_code: Optional[str] = None           # the full source (set by regex backend)
    extra: Dict[str, str] = field(...)            # backend-specific metadata
    effective_target: str = ""                   # post-fallback target (P1-2)
    effective_compile_db: str = ""               # post-fallback cdb path
```

Properties:
- `reduction_percentage` (line-based)
- `token_estimate` (chars/3.7 estimator)

`extra` is used for:
- `oracle: "true"` (clang backend)
- `clang_path`, `active_line_count` (clang backend)
- `inherited_target`, `inherited_sysroot`, `effective_target`, `effective_sysroot` (clang backend P4-1)
- `budget_degraded: "skeleton"` (when token budget forced skeleton)
- `budget_exceeded: "true"` (when neither pruned nor skeleton fit)
- `budget_pruned_tokens`, `budget_skel_tokens`, `budget_requested`

## The preprocessor engine

### `pruner_core.py` — the state machine

Stack-based. Each `ConditionalBlock` on the stack tracks:
- `directive` (`ifdef` / `ifndef` / `if` / `elif`)
- `condition` (string, e.g. `"PRODUCT_TYPE == 3"`)
- `state` (`ACTIVE` / `INACTIVE`)
- `taken` (bool, set when this branch was the first to match in its
  if/elif chain — prevents `#else` from firing after a match)

`process_line()` dispatches:
- `#ifdef X` / `#ifndef X` / `#if EXPR` → `_handle_if` → push block
- `#elif EXPR` → `_handle_elif` → update top block; if already taken, no-op
- `#else` → `_handle_else` → flip top block's state if not already taken
- `#endif` → `_handle_endif` → pop

For non-directive lines, the result is included if `is_currently_active()`
(all ancestors are ACTIVE) and the active state has not been taken
by a previous branch.

`skipped_ranges` is filled by tracking `current_skip_start` for
contiguous inactive regions. Bug-fix in P2: this field was
dead code (initialized but never populated) until P2.

### `expr_eval.py` — expression evaluator

Recursive-descent parser, no external dependencies. ~400 LOC.

Token types:
- `NUM` (int / float / hex)
- `DEF` (the `defined` keyword)
- `ID` (identifier — bare macro name)
- `OP` (operators)

Grammar:
```
expression := or_expr
or_expr    := and_expr ('||' and_expr)*
and_expr   := unary    ('&&' unary)*
unary      := '!' unary | primary
primary    := '(' expression ')' | comparison
comparison := additive (('==' | '!=' | '<' | '>' | '<=' | '>=') additive)?
additive   := multiplicative (('+' | '-') multiplicative)*
multiplicative := unary (('*' | '/' | '%') unary)*
unary      := ...
atom       := number | defined_call | identifier | macro_call
```

Special handling:
- `IS_ENABLED(X)` and `IS_BUILTIN(X)` are pre-expanded to `defined(X)`
  in a text-substitution pass before tokenization
- Hex literals are parsed with `int(val, 0)` (auto-detects 0x/0o/0b)
- Case-insensitive identifier matching (all keys lowercased)
- Bare identifier evaluates to 0 (undefined) or its numeric value

Error handling: `ValueError` on malformed input. The pruner
catches it and falls back to treating the block as inactive.

## The backends

### `backends/base.py`

- `PruneResult` dataclass
- `PrunerBackend` ABC with two abstract methods: `prune()` and `is_available()`
- `register_backend` decorator — puts the class into the `_REGISTRY` dict
- `get_backend(name, **kwargs)` factory:
  - `name == "auto"` → try clang, fall back to regex
  - `name == "regex"` / `"clang"` → instantiate directly
  - kwargs are forwarded to backend `__init__`
  - `TypeError` from backend (unknown kwarg) → retry without kwargs

### `backends/regex_backend.py`

Wraps `pruner_core.py` + `cc_parser.py`. ~80 LOC. The default.

### `backends/clang_backend.py` (P4-1 redesigned)

```python
class ClangBackend(PrunerBackend):
    def __init__(self, timeout=15.0, sysroot=None, extra_target=None):
        # sysroot / extra_target are user-supplied overrides;
        # auto-detected from the compile_db if None.

    def prune(self, file_path, target, compile_db, mode="physical"):
        # 1. Resolve and read the source.
        # 2. Build the base clang command: clang -E -w -x c|c++.
        # 3. Look up the compile_db entry, get the full token list.
        # 4. Filter through _filter_tokens_for_clang (drops gcc-specific
        #    flags, keeps --target, --sysroot, -march, -mabi, -I, etc.).
        # 5. Append the user's -D macros (from cc_parser.extract_macros).
        # 6. Append the user's -I dirs (from cc_parser.resolve_include_dirs).
        # 7. Apply the user's sysroot / extra_target overrides.
        # 8. Append the source file.
        # 9. Run clang as a subprocess.
        # 10. Walk the output line by line, tracking the "current
        #     original line" via # N "file" line markers.
        # 11. Mark lines as active when they're under a marker for
        #     our file AND contain non-marker content.
        # 12. Reconstruct skipped_ranges (complement of active_orig_lines).
        # 13. Return PruneResult with a banner explaining it's the oracle.
```

`_filter_tokens_for_clang` (the new bit in P4-1):

```python
_CLANG_FLAG_ALLOWLIST_PREFIXES = (
    "-D", "-I", "-isystem", "-iquote", "-include",
    "--target=", "--sysroot=", "-march=", "-mcpu=", "-mabi=",
    "-mfloat-abi=", "-mthumb", "-marm", "-f", "-std=", "-W",
    "-w", "-no-canonical-prefixes", "-pipe", "-pthread",
)
_CLANG_FLAG_ALLOWLIST_EXACT = {"-E", "-c", "-S", "-shared", "-static",
    "-nostdinc", "-nostdinc++", "-undef", "-x", "-Xclang"}
_DROP_PREFIXES = ("-Wl,", "-Wa,", "-Werror", "-save-temps",
    "-ftime-report", "--param=", "-z", "-static-libgcc",
    "-static-libstdc++", "-nodefaultlibs", "-nolibc",
    # Catch-all: drop ALL -f flags.
    "-f")
_VALUE_AS_NEXT = {"-I", "-isystem", "-iquote", "-include", "-x"}
```

The `-f` catch-all is the key insight. gcc's `-f` optimization flags
are legion (`-fno-tree-loop-distribute-patterns`,
`-fmerge-all-constants`, ...). Clang doesn't know most of them.
None of them affect `clang -E` output. So we drop them all.

The `INHERIT` step also strips `-D` / `-I` from the inherited list
(because we re-add them via the structured extractors), and skips
the source filename / `-o output` arguments (because we control
those ourselves).

## Caching layer

### `cc_parser.py` mtime cache (P1-3)

```python
_CACHE: Dict[str, Tuple[float, List[Dict]]] = {}  # path → (mtime, entries)
CACHE_MAX_ENTRIES = 16

def _cache_get(db_path: str) -> Optional[List[Dict]]:
    cached = _CACHE.get(db_path)
    if cached is None: return None
    cached_mtime, entries = cached
    try:
        current_mtime = Path(db_path).stat().st_mtime
    except OSError:
        _CACHE.pop(db_path, None); return None
    if current_mtime != cached_mtime:
        _CACHE.pop(db_path, None); return None
    return entries
```

Cap: 16 entries (LRU-by-mtime). Cleared on process restart.
Editable in tests via `clear_cache()`.

## Configuration layer

### `config.py` (P1-2)

Two-level structure: bare keys implicitly go into `[pruner]`,
sectioned keys use dot notation. Coercion handles bool/int/float/list.

Search order (first hit wins):
1. `$MACROPRUNER_CONFIG` env var
2. `<project>/.macroprunerrc` (or `macroprunerrc`)
3. `~/.macroprunerrc`
4. Built-in defaults

`project` is the directory of the file the tool is acting on
(NOT the agent's cwd). For the CLI, it's the directory of the
`file` argument.

## Error layer

### `errors.py` (P2-1)

`MacroPrunerError` hierarchy:
- `FatalError` (severity="FATAL") — call did not succeed
- `TransientError` (severity="WARN") — call succeeded with caveats

`format_error(exc)` maps stdlib exceptions:
- `FileNotFoundError` → `[FATAL]` with "check the path" hint
- `ValueError` → `[FATAL]` with "invalid arguments" hint
- `PermissionError` → `[FATAL]` with "permissions" hint
- Unknown → `[ERROR]` with `Type: Message`

`with_fallback(fn, *args, fallback_value=None, **kwargs)` — runs `fn`,
returns `fallback_value` on any exception except `FatalError` (which
propagates). Used by `read_c_with_deps` to skip individual dep
errors without failing the whole call.

## Patch layer

### `patch_applier.py` (P2-2)

Standalone unified-diff applier. ~300 LOC.

Why a custom implementation? The original `apply_patch` used
`git apply`, which is great when the file is in a git repo, but
most embedded firmware projects aren't. The custom applier
covers the common 80% case (single-file diffs with explicit line
offsets) without requiring git on the host.

`_parse_diff()` tokenizes the unified diff:
- Each `@@ -from,from_count +to,to_count @@` becomes a hunk tuple
- `# N "file"` markers and `--- a/` / `+++ b/` headers are stripped

`_apply_hunk()` matches the hunk against the source line by line,
raising `PatchError` on the first mismatch. No fuzzy matching by
design.

`apply_unified_diff(original, diff)` walks all hunks in order,
applying each and tracking `cumulative_net` (the sum of
`to_count - from_count` so far) to compute the correct adjusted
`from_line` for each subsequent hunk. This is the convention
used by `git apply` and `patch(1)`.

`check_c_syntax(content)` runs a lightweight post-apply validator:
- Brace balance
- `#if` / `#endif` balance
- Orphan `#else` / `#endif`
- Tracks string literals and comments so braces inside them
  don't count

A real patch that fixes a syntax error will trigger some of
these warnings; callers should treat them as hints, not failures.

## MCP integration

### `mcp_server.py` — the 4 tools

Each tool is decorated with `@server.tool(name=..., description=...)`
where the description includes:
- A one-line summary
- "Use when" / "Do NOT use when" guidance
- Full parameter reference

`read_c()` flow:
```python
@server.tool(name="read_c", description="...")
def read_c(file_path, target="", compile_db="", mode="physical",
           backend="regex", token_budget=0, sysroot="", extra_target=""):
    try:
        result = _prune_file(
            file_path, target, compile_db=compile_db, mode=mode,
            backend=backend, token_budget=token_budget,
            sysroot=sysroot, extra_target=extra_target,
        )
        if result.backend_name == "clang":
            return result.code  # clang has its own banner
        tok = result.token_estimate
        # ... build banner, return summary + code ...
    except FileNotFoundError as e:
        return FatalError(str(e), hint="...").formatted()
    except ValueError as e:
        return FatalError(str(e), hint="...").formatted()
    except Exception as e:
        return format_error(e)
```

`_prune_file()` is the common helper. It:
1. Loads `.macroprunerrc` if `file_path` or `compile_db` is empty
2. Resolves the file path (CWD-relative → absolute)
3. Calls `get_backend(backend, sysroot=sysroot, extra_target=extra_target)`
4. Optionally enforces token budget
5. Returns the `PruneResult`

## Extension points

The full extension point list is in [PLAN.md](../PLAN.md#extension-points).
Briefly: add a token operator in `expr_eval.py`, add a backend in
`backends/<name>_backend.py` with `@register_backend`, add a tool in
`mcp_server.py` with `@server.tool(...)`, add a CLI subcommand in
`cli.py`, add a config key in `config.py`'s `DEFAULTS`, etc.

## What runs when

Timing breakdown for a typical `read_c` call (host = Linux 5.15, Python 3.10):

| Step | Time | Cached? |
|---|---|---|
| MCP stdio JSON-RPC deserialization | ~5ms | n/a |
| `.macroprunerrc` load (parses file) | ~1ms (first time) | module-level cache |
| `get_backend("regex")` instantiation | <1ms | n/a |
| `cc_parser._load()` (parse cdb) | ~50ms (first time) | mtime cache |
| `pruner_core.prune()` | ~10ms | n/a |
| Banner formatting + return | <1ms | n/a |
| MCP stdio JSON-RPC serialization | ~5ms | n/a |
| **Total** | **~70ms first call, <20ms subsequent** | |

For a `read_c_with_deps` with 3 dependencies and `max_depth=2`:
~150ms first call, ~30ms subsequent (most time is walking 3 files).

For a `read_c` with `token_budget=80` that triggers skeleton
degradation: ~30ms total (skeleton is fast).

For the clang backend (cross-compile SDK with sysroot): ~500ms
(subprocess overhead dominates). Subsequent calls: same ~500ms
unless we add clang-side caching (not done; would be a v0.6 thing).
