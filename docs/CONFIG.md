# `.macroprunerrc` — Complete Reference

`.macroprunerrc` is the project-level configuration file. Drop one in
your project root and the LLM agent can call `read_c` / `read_c_skeleton`
/ `read_c_with_deps` without repeating the same flags every time.

## Quick start

```ini
# /path/to/your-firmware/.macroprunerrc

# Minimal config
default_target    = PRODUCT_3
compile_db        = build/compile_commands.json
default_backend   = regex

# Cross-compile SDK users (P4-1)
pruner.sysroot     = /opt/ws63-sdk/sysroot
pruner.extra_target = riscv32-linux-musl
```

That's it. Save the file in your project root and the next MCP call
inside that project picks it up automatically.

## Search order

When the tool needs a value, it searches in this order (first hit wins):

1. **The MCP call's argument** (highest priority)
   - `read_c(file_path="...", target="X", compile_db="...")`
2. **Environment variable** `$MACROPRUNER_CONFIG` (absolute path to a custom config file)
3. **`<project>/.macroprunerrc`** (or `<project>/macroprunerrc`)
4. **`~/.macroprunerrc`**
5. **Built-in defaults** (see "Defaults" below)

The "project" used in step 3 is the directory of the file the agent
is acting on, not the agent's CWD. So `python3 cli.py read
/any/path/main.c` from anywhere will pick up `/any/path/.macroprunerrc`
if present.

## Syntax

KEY = VALUE format. Lines starting with `#` are comments. Empty
lines are ignored. Optional `[section]` headers namespace keys under
a prefix.

```ini
# All of these set the same key:
default_target = PRODUCT_A
pruner.default_target = PRODUCT_A
[pruner]
default_target = PRODUCT_A
```

Bare keys (no `[section]`) implicitly belong to `[pruner]`. So the
short form `default_target = X` is equivalent to
`pruner.default_target = X`.

Unknown sections and unknown keys are kept under `_extra` for
forward-compat and ignored. This means you can stash your own
key=value pairs in the file without breaking the tool.

```ini
# .macroprunerrc
default_target = ws63
my_team_name = firmware-platform
debug_note = "see also product-team-config.md"

$ .venv/bin/python -c "from config import load; print(load())"
{'pruner.default_target': 'ws63', ..., '_extra':
  {'my_team_name': 'firmware-platform',
   'debug_note': 'see also product-team-config.md'}}
```

## Value coercion

Strings, numbers, booleans, and lists are all inferred from the
literal text:

| Literal | Coerced to |
|---|---|
| `42` | int `42` |
| `3.7` | float `3.7` |
| `true` / `yes` / `on` | bool `True` |
| `false` / `no` / `off` | bool `False` |
| `"foo"` or `'foo'` | string `foo` (quotes stripped) |
| `[a, b, c]` | list `[a, b, c]` (recursive coercion per element) |
| `anything else` | string (whitespace stripped) |

Numeric values that look like C hex (`0xFF`, `0o77`, `0b1010`) are
accepted by the `pruner` engine (not by the config parser, but they're
useful in `compile_commands.json` `-D` flags).

## All keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `pruner.default_target` | string | `""` | Default target for the MCP tools when omitted |
| `pruner.compile_db` | path | `""` | Path to `compile_commands.json` (relative to project root or absolute) |
| `pruner.default_backend` | string | `"regex"` | `regex` / `clang` / `auto` |
| `pruner.default_mode` | string | `"physical"` | `physical` / `virtual` |
| `pruner.default_max_depth` | int | `2` | `read_c_with_deps` traversal depth (1-5) |
| `pruner.token_budget` | int | `0` | Stage 4 cap. 0 = no cap. (P3-1) |
| `pruner.include_dirs` | list of strings | `[]` | Extra `-I` paths |
| `pruner.sysroot` | string | `""` | **Clang-only.** Cross-compile SDK sysroot path. (P4-1) |
| `pruner.extra_target` | string | `""` | **Clang-only.** `--target=` value (e.g. `riscv32-linux-musl`). (P4-1) |

## Resolution examples

### Example 1: Native Linux C project

Project: `/home/me/myapp/`, compile_db at `build/compile_commands.json`,
target name `release`.

```ini
# /home/me/myapp/.macroprunerrc
default_target = release
compile_db     = build/compile_commands.json
default_backend = auto     # clang if installed
```

LLM agent calls:
```
read_c(file_path="/home/me/myapp/src/main.c")
```

The tool:
- Resolves `target` → "release" from config
- Resolves `compile_db` → `/home/me/myapp/build/compile_commands.json`
- Uses the auto backend (clang here, since it ships with Ubuntu)

### Example 2: Cross-compile SDK project

Project: `/opt/ws63-firmware/`, cross gcc is `riscv32-linux-musl-gcc`,
sysroot at `/opt/ws63-sdk/sysroot`.

```ini
# /opt/ws63-firmware/.macroprunerrc
default_target     = ws63
compile_db         = output/ws63/acore/ws63-liteos-app/compile_commands.json
default_backend    = regex          # regex works for cross-SDK out of the box
default_max_depth  = 3

# If you also want to use clang for cross-validation:
pruner.sysroot     = /opt/ws63-sdk/sysroot
pruner.extra_target = riscv32-linux-musl
```

### Example 3: Token-budget-constrained

Project where the LLM has a 4K-token per-call budget.

```ini
default_target = ws63
compile_db     = build/compile_commands.json
token_budget   = 4000
```

When `read_c` produces an output > 4K tokens, the tool auto-degrades
to skeleton. The banner shows `Degraded: skeleton` so the LLM knows
why the function bodies are missing. See [docs/usage.md § 5.1](usage.md).

## `$MACROPRUNER_CONFIG` — point at a non-standard config

If you want to use a config file outside the search path (e.g.
`/etc/macroprunerrc` for system-wide defaults):

```bash
export MACROPRUNER_CONFIG=/etc/macroprunerrc
hermes mcp test macropruner
```

The env var is consulted **before** `.macroprunerrc` in the project,
so it acts as a global default that the per-project config can
still override.

## Inspection during development

```bash
.venv/bin/python -c "
from config import load
import json
print(json.dumps(load(), indent=2, default=str))
"
```

Shows exactly what the tool will see, including:
- The path the config was loaded from (`_config_path`)
- Any errors encountered while reading the file (`_config_error`)
- Unknown keys preserved under `_extra`

## Common mistakes

### Mistake 1: Path is relative to the wrong dir

```ini
# BAD — relative to whatever the agent's CWD is
compile_db = ../build/compile_commands.json

# GOOD — relative to the project root (where the .macroprunerrc lives)
compile_db = build/compile_commands.json
```

The tool resolves relative paths against the file's directory, not
the agent's CWD. So `../build/...` only works if the file the agent
is acting on is in a sibling of `build/`. The clean fix is to use
`build/compile_commands.json` and put `.macroprunerrc` at the
project root.

### Mistake 2: Section name typo

```ini
# BAD — typo, key not recognized
[prune]
default_target = ws63

# GOOD
[pruner]
default_target = ws63
```

If the section isn't `pruner`, the keys go into `_extra` and the
tool uses the default. The tool will not crash — it'll just be
silently wrong.

### Mistake 3: Forgetting the dot for nested keys

```ini
# BAD — unknown key
target = ws63

# GOOD — goes into [pruner]
default_target = ws63
# OR
pruner.default_target = ws63
```

Use `default_target` (the bare form, implicitly in `[pruner]`) or
`pruner.default_target` (fully qualified). Just `target` will not
be picked up.
