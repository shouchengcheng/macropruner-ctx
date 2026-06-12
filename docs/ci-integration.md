# CI integration — recipes for GitHub Actions, GitLab CI, Jenkins

This document shows how to drop macropruner-ctx into a team's
existing CI pipeline as a **PR smoke test**. The point is to flag
any change that accidentally breaks the macro-aware pruning — for
example, a refactor that introduces a `#if PRODUCT_TYPE` block the
LLM can't see, or a config change that makes the regex evaluator
mis-evaluate something.

## What this gives you

A PR check that runs:

```bash
# 1. Find a representative .c file in the build
# 2. Run read_c against it with two different targets
# 3. Verify the outputs are non-empty and token-positive
# 4. (Optional) Run a smoke diff against the .c's pre-PR output
```

If any of those fail, the PR is flagged. The LLM never sees the
diff, but the engineer does — the build log is human-readable.

This is **not** a replacement for the full test suite (which lives
in `test_*.py`). It's an end-to-end smoke that catches integration
breakage.

## GitHub Actions (recommended)

The repo ships `.github/workflows/tests.yml` which runs the unit
suite on every PR. To also run the macropruner smoke test, copy
`.github/workflows/example-pr-check.yml` into your fork's
`.github/workflows/`. It's opt-in (only runs on `workflow_dispatch`
by default, so fork PRs don't fail).

See [`.github/workflows/example-pr-check.yml`](../.github/workflows/example-pr-check.yml).

## GitLab CI

Drop this into your `.gitlab-ci.yml`:

```yaml
macropruner-smoke:
  stage: test
  image: python:3.12
  before_script:
    - python -m venv .venv
    - . .venv/bin/activate
    - pip install mcp
    # If you don't have compile_commands.json checked in, generate
    # it as part of the build (e.g. with bear, scan-build, etc.)
  script:
    # Adapt these two lines to your project:
    - TARGET_PR=PRODUCT_3
    - TARGET_TEST=PRODUCT_5
    - FILE=$(ls -1 src/main.c 2>/dev/null || ls -1 src/*.c | head -1)
    # Run read_c on each target, verify the output is non-empty.
    - .venv/bin/python cli.py read "$FILE" --target "$TARGET_PR" --cdb build/compile_commands.json | head -1
    - .venv/bin/python cli.py read "$FILE" --target "$TARGET_TEST" --cdb build/compile_commands.json | head -1
  artifacts:
    when: on_failure
    paths:
      - macropruner_smoke.log
  allow_failure: true  # Don't block merge; just log the smoke result.
```

`allow_failure: true` is intentional — macropruner is a tool the
LLM uses, not a build-time dependency. If the smoke fails, the
PR should still merge, but the engineer should investigate.

## Jenkins (declarative)

```groovy
pipeline {
    agent any
    stages {
        stage('macropruner smoke') {
            steps {
                sh '''
                    python3 -m venv .venv
                    . .venv/bin/activate
                    pip install mcp
                    FILE=$(find . -name 'main.c' -not -path '*/.venv/*' | head -1)
                    .venv/bin/python cli.py read "$FILE" --target PRODUCT_3
                '''
            }
        }
    }
}
```

## What to do when the smoke fails

1. **Check the banner.** The smoke test should print a banner with
   `Lines: 187/420 dropped (44.5%)` and `Tokens: 1230/2870 saved`.
   If those numbers go negative or absurd, something's wrong.
2. **Try with `mode="virtual"`.** That replaces inactive blocks with
   `[INACTIVE]` markers, so you can see exactly which lines the
   pruner thinks are active.
3. **Compare against a baseline.** Keep a `pruner-baseline.json` in
   the repo with the expected `target`, `lines`, `tokens` for each
   file. CI fails if the new numbers diverge by more than X%.
4. **Run `cli.py diff`.** This compares regex vs clang on the same
   file. If they disagree, regex has a bug.

## What NOT to do

- **Don't make the smoke required-to-merge.** Macropruner is
  downstream of your build. If the build passes but the smoke
  fails, you probably want a human to look, not a hard block.
- **Don't run the smoke on every commit.** The smoke is slow
  (0.1-0.5s per file). Once per PR is enough.
- **Don't compare pruned output byte-for-byte across runs.** The
  pruning is deterministic, but tool versions (clang, mcp) may
  change the banner format. Compare counts, not strings.

## See also

- `.github/workflows/tests.yml` — runs the unit test suite
- `.github/workflows/example-pr-check.yml` — opt-in smoke test
- `demo/demo.sh` — manual screencast-ready demo
- `integration/ws63_smoke.py` — the script that the smoke runs
- `docs/usage.md` — full operator's manual
