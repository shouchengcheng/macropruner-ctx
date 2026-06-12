# Prompt templates

Drop-in system-prompt snippets for LLM agents using macropruner-ctx.
Each template is a self-contained block of text — paste it into the
agent's system prompt, prepend it, or compose with other prompts as
your framework allows.

## What's here

| File | When to use | Tokens |
|---|---|---|
| [01-basic.md](01-basic.md) | The minimum. Use this if you only have room for one snippet. | ~250 |
| [02-cross-compile.md](02-cross-compile.md) | Project is a cross-compile SDK (riscv32-linux-musl, aarch64-linux-gnu, etc.) | ~350 |
| [03-token-budget.md](03-token-budget.md) | Hard per-call token budget; large files | ~250 |
| [04-oracle.md](04-oracle.md) | User questions the regex output; needs ground truth | ~300 |
| [05-safety.md](05-safety.md) | `MACROPRUNER_READONLY=1` is set; LLM must not write | ~400 |

## Composing prompts

These are designed to be additive. If your LLM uses a framework
that auto-generates tool lists, the snippets already work (just
trim the "two sentences on what each tool does" line from
01-basic.md).

For a typical project, the recommended composition is:

```
[system prompt boilerplate]
+ 01-basic.md
+ (if cross-compile SDK) 02-cross-compile.md
+ (if token budget)     03-token-budget.md
+ (if read-only mode)   05-safety.md
```

04-oracle.md is opt-in — most LLMs don't need it unless the user
is actively cross-checking.

## Token budget for prompts

These add up. A typical mid-size project with all five prompts
plus tool auto-generation is ~2000 tokens of system prompt
overhead. That's a real cost on long sessions. Pick what you
actually need:

- **Minimum:** 01-basic.md (~250 tokens)
- **Common case:** 01 + 02 or 01 + 03 (~600 tokens)
- **Full safety:** 01 + 02 + 03 + 05 (~1300 tokens)
- **Maximum:** all five (~1500 tokens)

## See also

- [docs/usage.md](../usage.md) — the full operator's manual
- [docs/ERRORS.md](../ERRORS.md) — the `[FATAL]` / `[WARN]` error protocol
- [docs/CONFIG.md](../CONFIG.md) — `.macroprunerrc` reference
