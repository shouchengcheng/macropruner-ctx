# Prompt 4: Cross-validation oracle — when regex output is suspicious

For LLMs that occasionally cross-check regex-backend output against
the clang oracle. Use this when:
- A user asks "is this code path actually active?"
- A user is debugging a hallucinated struct/function
- An LLM is doing a code review and wants ground truth

```
This session has access to macropruner-ctx's clang backend
for cross-validation. The regex backend is fast and usually
correct, but the clang backend is the actual preprocessor
running on the file — it is the ground truth.

When to cross-check:
  - User explicitly questions the regex output ("are you sure
    this branch is active?")
  - You need to know whether a struct field or function actually
    exists in the compiled code (clang's #include expansion
    surfaces real types)
  - The regex output has a [WARN] tag

How to cross-check:
  1. read_c(file_path=X, target=T, backend='clang')
  2. Compare the result with the regex output (use a `diff`
     command or just look at both)
  3. If they disagree, the clang output is the truth (it's running
     the actual compiler)
  4. The most common disagreement: regex considers line 1
     ("#include ...") active but clang may not tag it (the
     include is inlined, no line marker for it). Treat this as
     a benign difference.

When the user is doing a serious code review, run both
backends on the same file and surface the diff in your
response.
```

## Why this snippet

- **Teaches the LLM that the disagreement direction matters.** LLMs
  by default treat `diff` output as "errors to fix" rather than
  "oracle vs approximation." This prompt tells them clang wins.
- **Explains the most common false disagreement** (line 1
  include not being tagged). Without this, an LLM will report
  "regex and clang disagree!" every time and exhaust the user's
  attention.
- **Frames the oracle as a debug tool, not a default.** The
  regex backend is the daily driver. The clang backend is for
  "the user is worried" moments. Without this framing, LLMs
  default to the oracle (because it sounds more authoritative)
  and slow every read_c to ~500ms.

## Tailoring

If your project is a cross-compile SDK, add a sentence:

```
  For cross-compile SDKs, the clang backend may need
  pruner.sysroot and pruner.extra_target in .macroprunerrc.
  Without these, the oracle will [FATAL] on missing headers.
```

That tells the LLM to check `.macroprunerrc` before recommending
the oracle.

## When NOT to use this snippet

If the project is a small, single-product codebase where regex
output is reliable, this snippet is overhead. Just use
[01-basic.md](01-basic.md).

If the user is doing a one-shot code review (not a long-running
session), the oracle adds 0.5s per call which is fine; you
don't need to teach the LLM the diff dance. The basic prompt
is enough.
