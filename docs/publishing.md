# How to publish to PyPI

This is a one-time setup. Once configured, publishing a new version
is a single command.

## One-time setup

### 1. Create accounts

- **PyPI:** https://pypi.org/account/register/
- **Test PyPI:** https://test.pypi.org/account/register/ (use this first to verify the upload looks right)

### 2. Get a PyPI API token

- PyPI: https://pypi.org/manage/account/token/ — create a token
  scoped to the project (or "Entire account" if you don't have
  project-specific scopes yet)
- **Important:** Save the token immediately. PyPI only shows it
  once.

### 3. Configure `~/.pypirc`

```ini
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = pypi-YourRealTokenHere

[testpypi]
username = __token__
password = pypi-YourRealTestTokenHere
```

File mode: `chmod 600 ~/.pypirc` (PyPI refuses to read world-writable files).

### 4. Install the build tooling

```bash
.venv/bin/pip install --upgrade build twine
```

(`build` is the PEP 517 build frontend; `twine` is the secure uploader.)

## Publishing a new version

### Step 1: bump the version

In `pyproject.toml`:
```toml
[project]
version = "0.5.1"   # was 0.5.0
```

Follow [semver](https://semver.org/): `MAJOR.MINOR.PATCH`.
- Bump MAJOR for breaking API changes (we're at 0, so this is a 1.0 candidate moment)
- Bump MINOR for new features (P12 safety features = 0.5.0 → 0.6.0)
- Bump PATCH for bugfixes

### Step 2: build the artifacts

```bash
.venv/bin/python -m build
```

This produces `dist/macropruner_ctx-0.5.0.tar.gz` and
`dist/macropruner_ctx-0.5.0-py3-none-any.whl`.

### Step 3: sanity check the artifacts

```bash
# What's inside?
tar tzf dist/macropruner_ctx-0.5.0.tar.gz | head -20

# Does the wheel have the right metadata?
.venv/bin/python -m zipfile -e dist/macropruner_ctx-0.5.0-py3-none-any.whl /tmp/wheel-check/
ls /tmp/wheel-check/macropruner_ctx-0.5.0.dist-info/

# Does the entry point work in an isolated venv?
.venv/bin/python -m venv /tmp/install-test
/tmp/install-test/bin/pip install dist/macropruner_ctx-0.5.0-py3-none-any.whl
/tmp/install-test/bin/macropruner --help
```

### Step 4: upload to Test PyPI first

```bash
.venv/bin/python -m twine upload --repository testpypi dist/macropruner_ctx-0.5.0*
```

Visit https://test.pypi.org/project/macropruner-ctx/ to confirm the
package page looks right (description renders, links work, etc.).

### Step 5: install from Test PyPI and verify

```bash
.venv/bin/python -m venv /tmp/testpypi-install
/tmp/testpypi-install/bin/pip install --index-url https://test.pypi.org/simple/ macropruner-ctx
/tmp/testpypi-install/bin/macropruner read test_samples/test_main.c --target ENABLED_FEATURE --cdb compile_commands.json
```

### Step 6: upload to real PyPI

```bash
.venv/bin/python -m twine upload dist/macropruner_ctx-0.5.0*
```

### Step 7: verify

```bash
.venv/bin/python -m venv /tmp/realpypi-install
/tmp/realpypi-install/bin/pip install macropruner-ctx
/tmp/realpypi-install/bin/macropruner read test_samples/test_main.c --target ENABLED_FEATURE
```

Visit https://pypi.org/project/macropruner-ctx/ to see the live page.

## What goes in the wheel

The `pyproject.toml` declares:

```toml
[tool.setuptools]
py-modules = [cli, mcp_server, pruner_core, ...]
packages = [backends]
```

That means the wheel contains:
- All the `.py` files in the repo root (cli, mcp_server, pruner_core, ...)
- The `backends/` subpackage

**Not** included (intentionally):
- `.venv/`
- `.git/`
- `test_samples/` (the test fixtures are not part of the runtime)
- `test_*.py` files (the test suite is dev-only; users get it via GitHub clone)
- `.zhiyu/`, `.cache/`, `.ruff_cache/`

These are excluded by the build backend's default rules. If you add
a new top-level `.py` file, add it to `py-modules` in `pyproject.toml`.

## GitHub Actions automatic publish

For hands-free releases, add a `.github/workflows/publish.yml`:

```yaml
name: publish

on:
  push:
    tags: ['v*']

jobs:
  pypi:
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write  # for PyPI's trusted publishing
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Build
        run: python -m build
      - name: Publish
        uses: pypa/gh-action-pypi-publish@release/v1
        # No password needed — uses trusted publishing.
```

This requires enabling "Trusted Publishing" on PyPI for the project
(https://pypi.org/manage/project/macropruner-ctx/settings/publishing/).
The workflow above shows a minimal config; for production you'll
want to add a release job, a changelog check, etc.

## See also

- [docs/usage.md](../docs/usage.md) — full operator's manual
- [docs/CHANGELOG.md](../docs/CHANGELOG.md) — version history
- [https://packaging.python.org/tutorials/packaging-projects/](https://packaging.python.org/tutorials/packaging-projects/) — the official PyPA guide
