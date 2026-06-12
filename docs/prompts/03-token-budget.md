# Prompt 3: Token budget — when you have a hard per-call cap

Use this when the LLM has a hard per-call token budget (e.g. 4K
tokens per tool response) and the source files are large. The
prompt teaches the LLM to lean on `read_c_skeleton` and to
expect the `[WARN] Over budget` banner.

```
This session has a hard per-call token budget. macropruner-ctx
honors this via the `token_budget` parameter on read_c:

  read_c(file_path="src/big.c", token_budget=2000)

If the pruned output is under the budget, you get the full
pruned code. If it's over, the tool auto-degrades to a
skeletonized view (function bodies stripped). The banner
will show either:

  /* Degraded: skeleton                   */
  /* [WARN] Over budget: pruned=N, skel=M, cap=K */

Strategy for large files:
  1. First try: read_c with token_budget=2000
  2. If still over (banner shows [WARN]): read_c_skeleton
     with no budget, get the structural view
  3. If you need function bodies: split the file by hand and
     read each chunk separately

Do NOT retry the same call with a higher budget — if 2000
isn't enough, ask the user to either chunk the request or
disable the budget for this conversation.

When the banner shows "Degraded: skeleton", you can call
read_c_skeleton explicitly to see the same content without
the auto-degrade noise.
```

## Why this snippet

- **The strategy chain is explicit.** LLMs default to retrying the
  same thing with slightly different parameters. This prompt
  shows the right escalation path.
- **The "do NOT retry" warning.** Without it, an LLM that hits
  the cap will try `token_budget=2001`, `token_budget=3000`,
  `token_budget=5000`, etc., each failing. With the warning,
  the LLM skips the retry loop and asks the user.
- **The two specific values.** `2000` is a sensible default
  for a 4K total context. If your budget is different, edit
  the prompt.

## Tailoring

Replace `2000` with your actual budget. The three-step escalation
is the same regardless of the number:

```
1. read_c with token_budget=BUDGET
2. read_c_skeleton if [WARN] appears
3. ask the user to chunk if neither works
```

## When NOT to use this snippet

If the project is small (every file is under 1K tokens even
unprocessed) or the LLM has plenty of headroom (10K+ per call),
this snippet is over-engineering. Just use [01-basic.md](01-basic.md).
