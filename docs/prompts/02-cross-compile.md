# Prompt 2: Cross-compile SDK — for HiSilicon ws63 / aarch64 / etc.

Drop this into the system prompt when the project is a
cross-compile firmware SDK (riscv32-linux-musl, aarch64-linux-gnu,
etc.). It teaches the LLM the cross-compile specifics so it doesn't
try to use the clang backend without the right sysroot.

```
This project is a cross-compile firmware SDK. The build uses a
custom toolchain (e.g. riscv32-linux-musl-gcc), and the project
lives at a sysroot that is NOT on the host's default search path.

For macropruner-ctx:
  - The regex backend works without configuration. Just call
    read_c as usual.
  - The clang backend (ground-truth oracle) needs to know where
    the sysroot is. The project should have a .macroprunerrc with
    `pruner.sysroot` set. If you call read_c with backend='clang'
    and get [FATAL] RuntimeError mentioning missing headers, the
    sysroot is wrong; do NOT retry the same call, ask the user to
    update .macroprunerrc.

Common [FATAL] patterns on cross-compile SDKs:
  - "port/header.h file not found"  →  pruner.sysroot is missing
  - "unknown target CPU 'rv32X'"     →  pruner.extra_target is missing
  - "unknown argument '-fno-XXX'"   →  known issue; ignore (these
                                       are gcc-only flags; the regex
                                       backend filters them; clang
                                       doesn't recognize them but
                                       they're harmless)

When the user asks "is this code path active in our product?",
prefer:
  1. read_c with the user's product name as target
  2. If the user questions the result, read_c with backend='clang'
     to cross-check
  3. If they still question, ask the user to verify the cdb's
     -D flags against the actual product build

Token saving is the same as for native projects: 7%-87% depending
on how much inactive code is in the file.
```

## Why this snippet

- **Teaches the LLM the "sysroot" concept.** Most LLMs don't know
  what a sysroot is. Without this, they'll see the [FATAL]
  and either retry blindly or assume the tool is broken.
- **Three common error patterns.** LLMs pattern-match on error
  messages. Showing them the three most common ones (with their
  meaning) means they can self-correct instead of pestering the
  user.
- **The decision tree at the end.** LLMs do better with explicit
  "if you want X, do Y; if you want Z, do W" than with a flat
  list of capabilities. This gives them a default + an escape
  hatch.

## Tailoring

The example toolchain in the prompt is `riscv32-linux-musl-gcc` (ws63).
If your SDK uses a different triplet, change it. The three
common error patterns are stable across most cross-compile SDKs
because the underlying toolchain limitations are the same.

If your project does NOT use the clang oracle (which is the
common case — most teams only use regex), you can drop the
oracle section and the decision tree at the end:

```
This project is a cross-compile firmware SDK. The build uses
riscv32-linux-musl-gcc.

For macropruner-ctx:
  - The regex backend works without configuration. Just call
    read_c as usual.
  - The clang backend (ground-truth oracle) is not configured
    for this project; the project would need pruner.sysroot in
    .macroprunerrc to use it. Use the regex backend unless
    specifically asked for cross-validation.
```
