# Prompt 5: Safety — preventing accidental writes

For LLMs running in shared / production / CI environments where
the LLM must NOT modify any files. Pair this prompt with the
`MACROPRUNER_READONLY=1` environment variable on the host.

```
This session uses macropruner-ctx in READ-ONLY mode. The
host has set MACROPRUNER_READONLY=1.

In read-only mode:
  - read_c, read_c_skeleton, read_c_with_deps work as normal
  - apply_patch is REFUSED. Any call to apply_patch will return
    a [FATAL] error: "apply_patch refused: MACROPRUNER_READONLY=1
    is set"

If you (the LLM) need to suggest a change, OUTPUT THE DIFF in
your response. Do not call apply_patch. The human reading your
response can apply it manually after review.

Example: instead of
  apply_patch(file_path="src/main.c", diff="...")
just say in your response:
  "Here's the change I'd suggest for src/main.c:
   @@ -10,3 +10,4 @@
      int a = 1;
   +  int b = 2;
      return a;
  Please review and apply manually."

This is enforced at the tool boundary, not by you. The MCP
server will refuse apply_patch regardless of what the LLM does.
The env var is a hard lock; even if you somehow get around the
prompt, the tool will still refuse.
```

## Why this snippet

- **Matches the actual enforcement layer.** The prompt tells the
  LLM "this is enforced at the tool boundary, not by you". If the
  prompt had said "you should not call apply_patch", the LLM
  might try anyway. By making the env var the source of truth,
  the prompt and the runtime agree.
- **Gives a concrete alternative output format.** LLMs default to
  calling tools. "Don't call apply_patch" without an alternative
  leaves the LLM confused. The example shows how to embed the
  diff in the response.
- **Acknowledges the user's review workflow.** Read-only mode
  exists because there's a human in the loop who wants to
  review changes. The prompt makes that explicit.

## Tailoring

If your project is a CI / batch / scheduled job (no human
reviewing in real time), the right alternative is to write
the diff to a file:

```
If you need to suggest a change, write the diff to a file in
this conversation's scratch directory. The CI runner will
pick it up and apply it after the LLM is done.

Example:
  write_to_scratch("src/main.c.patch", diff_content)
```

That requires a `write_to_scratch` tool, which you can build
on top of the standard filesystem write tool. Adjust this
prompt to match what your agent actually supports.

## When NOT to use this snippet

If the LLM is the only writer (interactive development with a
human in the loop who reviews each call), read-only mode is
overkill. The user can always undo. Just use [01-basic.md](01-basic.md).

If `MACROPRUNER_READONLY=1` is NOT set, this prompt is misleading
because apply_patch will actually work. The prompt and the
runtime should agree; pick one or the other, not both inconsistently.
