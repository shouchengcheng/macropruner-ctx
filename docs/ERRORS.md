# Errors — Tagged Output Protocol

Every tool return value is a string. When something goes wrong, the
string is prefixed with a severity tag the LLM (or your test suite)
can grep for.

## The three tags

### `[FATAL]` — call did not succeed

The user must fix something before retrying. Examples:

- File not found (`read_c` on `/no/such/file.c`)
- `compile_commands.json` not found or unparseable
- Invalid arguments (e.g. `apply_patch` with a diff that doesn't
  match the current file)
- Internal error preventing any output

The LLM should treat `[FATAL]` as: "this call did not succeed,
adjust the arguments and try again."

### `[ERROR]` — unexpected internal failure

The tool itself crashed or hit a path it didn't expect. Examples:

- Parser bug
- IO error mid-call (e.g. disk full, permission revoked)
- Library incompatibility (e.g. `clang` binary doesn't support a flag we passed)

The LLM should treat `[ERROR]` as: "this call did not succeed, but
the failure is on the tool's side, not the caller's. Maybe retry;
maybe report."

### `[WARN]` — call succeeded with caveats

The tool got something useful, but not everything it tried. Examples:

- `read_c_with_deps` couldn't read one of the dependency files
- `apply_patch` applied the diff but the post-apply syntax check
  flagged unbalanced `#if` / `#endif`
- Token budget exceeded even after auto-degradation to skeleton

The LLM should treat `[WARN]` as: "this call worked, but with caveats;
consider mentioning it in your response."

## Format

The tag is the first non-whitespace token on its line. A `hint:`
line may follow for `[FATAL]` / `[WARN]`.

```
[FATAL] compile_commands.json not found: 
  hint: verify the path exists, or drop a .macroprunerrc with 'compile_db = ...'

[WARN] Over budget: pruned=1850, skel=711, cap=80

[ERROR] ValueError: bad input
```

The LLM prompt can include a one-liner like:

> When a tool call returns `[FATAL]`, the call did not succeed. Adjust
> the arguments and try again. `[WARN]` means the call worked but with
> caveats.

## What the tags are NOT

- **Not HTTP status codes.** There is no equivalent of 200/404/500.
  The body is always text; the tag prefixes the text.
- **Not JSON.** Tool return values are plain text, not structured.
  If you need structured output, the LLM can parse the body, but
  the tag itself is a stable string.
- **Not always shown.** When everything works, the tool returns
  the code with a `/* --- MacroPruner-Ctx --- */` banner. No
  severity tag in the success case.

## How each tool reports errors

### `read_c` / `read_c_skeleton` / `read_c_with_deps`

| Situation | Tag | Example |
|---|---|---|
| File not found | `[FATAL]` | `[FATAL] Cannot resolve file path: /no/such/file.c` |
| `compile_commands.json` not found | `[FATAL]` | `[FATAL] compile_commands.json not found: <path>` + hint about `.macroprunerrc` |
| Backend unavailable, falls back to regex | (silent, regex result returned) | The banner shows `Backend: regex` |
| Token budget exceeded (skeleton fits) | (silent, banner shows `Degraded: skeleton`) | No `[WARN]` in body, but the banner line tells the LLM |
| Token budget exceeded (skeleton doesn't fit either) | `[WARN]` | Banner shows `[WARN] Over budget: pruned=N, skel=M, cap=K` |
| One dependency file unreadable (`with_deps`) | (silent, others returned) | The missing dep is omitted from the output |
| Invalid arguments (e.g. wrong type) | `[FATAL]` | `[FATAL] <TypeError message>` + hint |

### `apply_patch`

| Situation | Tag | Example |
|---|---|---|
| Patch applied successfully | `[OK]` (no severity) | `[OK] Patch applied to main.c via builtin.` |
| Patch applied but syntax check found issues | `[OK]` + `[WARN]` | `[OK] Patch applied to main.c via builtin. [WARN] Syntax check found 2 issue(s): ...` |
| Patch context mismatch (offset drift) | `[FATAL]` | `[FATAL] hunk context mismatch at line 7: diff says: 'X' file has: 'Y'` + hint to regenerate diff |
| File not found | `[FATAL]` | `[FATAL] Cannot resolve file path: ...` |
| `git apply` failed (when in a git repo) | `[FATAL]` | `[FATAL] git apply failed: <stderr>` |

## Why tagged strings, not MCP error protocol

MCP supports a structured error response (the tool returns
`isError: true` and an error code). But:

- Many MCP clients (including some versions of Claude Desktop)
  swallow `isError` errors silently and surface only the text
  content to the LLM.
- A tagged string always reaches the LLM as text, which is the
  format the LLM is best at processing.
- Stable string prefixes are easy to grep in tests.

So we return the error information as text, prefixed with a
severity tag. The LLM can decide what to do with it.

## Custom error subclasses

If you're writing your own tool that wraps macropruner, the
`errors.py` module provides:

```python
from errors import FatalError, TransientError, format_error, with_fallback

# Raise a fatal error
raise FatalError("compile_db path is wrong", hint="set pruner.compile_db in .macroprunerrc")

# Format a stdlib exception
formatted = format_error(FileNotFoundError("/no/such/file.c"))
# → "[FATAL] /no/such/file.c\n  hint: check that the file exists and the path is correct"

# In a per-dep loop, skip the bad one and keep going
def try_something(path):
    return some_risky_call(path)

result = with_fallback(try_something, "/some/dep.h", fallback_value=None)
# → if try_something raised FatalError, it propagates
#   if it raised anything else, with_fallback returns None
```

`with_fallback` is what `read_c_with_deps` uses internally: one
bad dep file emits a `[WARN]` and the rest of the call continues
with the other deps.

## Test-side grep examples

```python
# In test_mcp_server.py:
result = await session.call_tool("read_c", {"file_path": "/no/such.c"})
assert "[FATAL]" in result.content[0].text
assert "Cannot resolve file path" in result.content[0].text

# In a custom CI test:
import subprocess
out = subprocess.run(["cli.py", "read", "/no/such.c"], capture_output=True, text=True)
assert out.returncode == 1
assert "[FATAL]" in out.stderr
```

The tags are stable contracts — don't change them without a major
version bump. The hint text and exception messages can change
freely.
