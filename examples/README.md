# Real-SDK integration template

This directory holds **drop-in templates** for running macropruner-ctx
against a real cross-compile SDK checkout (e.g. HiSilicon WS63, aarch64
vendor SDKs, or any in-house firmware SDK). It replaces the previous
`integration/ws63_smoke.py` that shipped with the repo.

> macropruner-ctx itself is **SDK-agnostic** — it reads whatever
> `compile_commands.json` you point it at. You just need to write
> the right `.macroprunerrc` and call the CLI / MCP tools.

---

## 1. Set up a project config

In the root of your firmware project (NOT in macropruner-ctx's
directory), drop a `.macroprunerrc`:

```ini
# <your-firmware-project>/.macroprunerrc

# Default target — match the active product/board/chip variant.
# The MCP tools' `target` parameter is the `target` macro you want
# the pruner to treat as "defined" (along with whatever -D flags
# are in the cdb entry for that file).
default_target = ws63

# Path to the SDK's compile_commands.json (relative to project root
# or absolute). On multi-app SDKs, pick the variant that matches
# the build you're analyzing.
compile_db     = <path-to-sdk>/output/<product>/<app>/compile_commands.json

default_backend   = regex          # regex always works cross-SDK
default_mode      = physical
default_max_depth = 3

# For the clang oracle (optional, see docs/BACKENDS.md):
pruner.sysroot      = <cross-sdk-sysroot>
pruner.extra_target = riscv32-linux-musl
```

Field reference: [`docs/CONFIG.md`](../docs/CONFIG.md).

---

## 2. Pick a representative source file

A good showcase file:

- has multiple `#if`/`#ifdef`/`#ifndef` directives (≥ 5)
- is a real product source file, not a sample or test stub
- sits in a directory whose `#if` macros are well covered by the
  `compile_commands.json` `-D` flag list (so the demo numbers
  reflect your real product, not the worst case where every
  `#if`'s macro is undefined)

The legacy `pick_representative_c_file()` heuristic from the
old `ws63_smoke.py` is now a one-liner in any LLM agent prompt:

```python
# Pseudo-code for an LLM-driven pick:
#   1. Load the cdb
#   2. For each .c file in <product>/<app>/ source dirs:
#        n_if = count("^\\s*#\\s*(if|ifdef|ifndef)")
#        if n_if < 5: skip
#        macros = set(re.findall(r"-D([A-Z_][A-Z0-9_]*)", cdb_entry["command"]))
#        covered = # of "#if MACRO" where MACRO ∈ macros
#        score by (covered/n_if, n_if, file_size)
#   3. Pick the highest-scoring file
```

---

## 3. Drive macropruner against the SDK

### CLI (no MCP server required)

```bash
# From your firmware project root, with macropruner-ctx cloned
# somewhere reachable. The simplest form:

MACROPRUNER=/path/to/macropruner-ctx

# Prune
$MACROPRUNER/.venv/bin/python $MACROPRUNER/cli.py read \
    src/middleware/foo.c

# Skeletonize
$MACROPRUNER/.venv/bin/python $MACROPRUNER/cli.py skeleton \
    src/middleware/foo.c

# regex vs clang oracle
$MACROPRUNER/.venv/bin/python $MACROPRUNER/cli.py diff \
    src/middleware/foo.c
```

`.venv/bin/python` and `cli.py` live in the macropruner-ctx repo;
the `.macroprunerrc` lives in your firmware project root.
`cli.py` searches for the rc starting from the file's directory
and walking up — no extra flag needed.

### MCP (LLM agent)

Add to your agent's MCP config:

```bash
# Hermes
hermes mcp add macropruner \
    --command "/path/to/macropruner-ctx/mcp_wrapper.sh"
```

From inside the LLM session, the agent can now call:

```
read_c(file_path="src/middleware/foo.c")
# → banner: Target=ws63, Lines=X/Y dropped, Tokens=Z/W saved
# → fully preprocessed C source
```

No `target` or `compile_db` argument needed — they're picked up
from your firmware project's `.macroprunerrc`.

---

## 4. What to expect on real code

Empirically, real embedded SDKs see **7% – 87% token savings**
depending on the file:

| File class | Token savings |
|---|---|
| Well-covered middleware (macros defined in cdb) | 7% – 30% |
| Multi-include driver (108+ includes, conditional) | 25% – 40% |
| Driver with most `#if` macros undefined in cdb | 70% – 87% |
| API surface (`read_c_skeleton`, function bodies stripped) | ~80% |

The `cli.py diff` subcommand is your CI oracle check: it runs both
`regex` and `clang` backends on the same file and reports any
disagreement in the active-line set.

---

## 5. Common gotchas

- **`clang -E` fails with `'foo.h' file not found`** on a
  cross-compile SDK → set `pruner.sysroot` and
  `pruner.extra_target` in `.macroprunerrc`. Or just use
  `default_backend = regex` (the default for cross-SDKs).
- **`compile_commands.json` is 30 MB and slow to parse** → the
  per-process mtime cache makes subsequent calls O(1); the first
  call is the only slow one.
- **Pruning yields `target=DEFAULT` with no active macros** → the
  `target` you passed doesn't match any cdb `-D` flag. Inspect
  the cdb: `python -c "import json; print([t for e in json.load(open('build/compile_commands.json')) for t in e['command'].split() if t.startswith('-D')][:20])"`.
- **`cli.py diff` exits non-zero on disagreement** → that's
  expected; the exit code is 2 when the two backends disagree on
  the active-line set. CI smoke flows should treat 2 ≠ 1 and
  read the printed ranges.

---

## 6. If you want a fully automated smoke script

The previous `integration/ws63_smoke.py` automated the steps above
against the WS63 SDK specifically. If you want the same level of
automation for your own SDK, copy [this template][template] into
your own repo (not this one) and adapt:

```python
SDK_ROOT = Path(os.environ["YOUR_SDK_ROOT"])  # inject via env var
PRIMARY_CDB = SDK_ROOT / "output" / "<product>" / "<app>" / "compile_commands.json"
```

Don't hard-code absolute paths in scripts that go into a public
repo — always take them from `os.environ`.

[template]: https://github.com/shouchengcheng/macropruner-ctx/issues/new "Open an issue if you want the original ws63_smoke.py logic back as a generic script"
