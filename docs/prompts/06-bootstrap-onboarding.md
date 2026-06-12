# Prompt 6: Onboarding — first-time setup via bootstrap_config

Use this as the LLM's system prompt for the first session in a
project where the LLM needs to set up macropruner-ctx itself.
After the LLM has called `bootstrap_config(apply=True)` once,
you can drop this prompt and switch to [01-basic.md](01-basic.md).

```
You are starting work in a new project. The project may or may
not already have a .macroprunerrc (the macropruner-ctx config
file). If it doesn't, the MCP tool list will include a
`bootstrap_config` tool — that's the hint that config setup is
needed.

Onboarding procedure:
  1. Try `read_c(file_path="src/main.c")` (or any C file).
     - If you get pruned code: config is already set up. Skip
       to step 3.
     - If you get [FATAL]: config is missing. Continue to step 2.
  2. Call `bootstrap_config()` (dry-run). Read the recommendation.
     Verify the target name and compile_db path look right.
     If yes, call `bootstrap_config(apply=True)`.
     If the recommendation looks wrong, STOP and ask the user
     to clarify before applying.
  3. From here on, use `read_c` as normal. The config will
     supply target / compile_db / path_allowlist automatically.

What bootstrap_config does:
  - Scans PROJECT_MANIFEST.md (init-project skill) or
    compile_commands.json
  - Infers default_target from the most common -D flag set
  - Writes the config to a project-level location (NOT
    a shared /etc/ location)
  - Refuses to overwrite an existing file unless you pass
    force=True

What bootstrap_config does NOT do:
  - Does not modify your source code
  - Does not modify PROJECT_MANIFEST.md
  - Does not run any builds
  - Does not touch .git/ or anything outside the project

When to ask the user:
  - The recommended target name doesn't match what you see
    in #ifdef blocks (e.g. cdb says PRODUCT_TYPE=3 but the
    source uses #ifdef WIFI_A)
  - Multiple projects are active in the manifest and you're
    not sure which one to use
  - The compile_db path is wrong (e.g. points to a stale
    .json from a previous build)
```

## Why this snippet

- **The branching logic (step 1 → 2 vs skip to 3) is explicit.**
  Without it, an LLM in a project that already has a config
  would unnecessarily call `bootstrap_config` and possibly
  overwrite a working file.
- **The "what it does NOT do" list is critical for safety.**
  An LLM that misunderstands `bootstrap_config` as a "rewrite
  my source" tool will refuse to use it. Listing the negative
  cases (does not modify source / manifest / build) gives
  the LLM the right mental model.
- **The "when to ask" list prevents misconfig.** LLM
  confidence is calibrated; a wrong target name is a 1-line
  error that becomes a 30-minute debugging session if it
  silently picks the wrong one.

## Tailoring

For cross-compile SDKs, append:

```
For cross-compile SDKs:
  - The bootstrap_config may not detect pruner.sysroot and
    pruner.extra_target. If you see [FATAL] RuntimeError
    with "unknown target CPU" or "header file not found"
    after applying the bootstrap, the cross-compile target
    is missing. Ask the user for the sysroot path and edit
    .macroprunerrc manually:
      pruner.sysroot = <path to SDK sysroot>
      pruner.extra_target = <target triplet, e.g. riscv32-linux-musl>
```

This is the one case where bootstrap can't fully automate —
cross-compile SDKs have sysroot paths that are not in any
manifest. The prompt teaches the LLM to ask instead of guess.

## After onboarding

After `bootstrap_config(apply=True)` succeeds, you can:

1. Drop the bootstrap prompt and use [01-basic.md](01-basic.md)
2. Add the project-specific prompts ([02-cross-compile.md](02-cross-compile.md)
   if cross-SDK, [03-token-budget.md](03-token-budget.md) if budget-tight, etc.)
3. The user reviews the generated `.macroprunerrc` and commits it

For follow-up sessions in the same project, the tool list will
**not** include `bootstrap_config` (because the config already
exists), so this prompt is only relevant for the first session.

## See also

- [docs/usage.md](../usage.md) — the full operator's manual
- [docs/CONFIG.md](../CONFIG.md) — `.macroprunerrc` reference
- [INTEGRATION.md](../../INTEGRATION.md) — the bootstrap chapter
- [01-basic.md](01-basic.md) — the post-onboarding prompt
