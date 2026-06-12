"""Tests for P14 bootstrap_config: auto-generate .macroprunerrc.

Covers:
  - _find_manifest walks up directories
  - _parse_manifest extracts active project correctly
  - _select_active_project: name match, then active: true
  - _infer_target_from_cdb: PRODUCT_/CHIP_/BUILD_TYPE_ macros
  - _infer_target_from_cdb: canonicalization (PRODUCT_TYPE_3 -> PRODUCT_3)
  - _infer_target_from_cdb: falls back to DEFAULT
  - scan() with init-project manifest: source, target, rc_path correct
  - scan() without manifest: heuristic finds cdb in build/
  - apply() writes file
  - apply() refuses to overwrite without force
  - apply() with force=True overwrites
  - _format_rc renders sensible output
  - config.py's _find_initproject_active_rc integrates correctly
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ── _find_manifest ──────────────────────────────────────────────


def test_find_manifest_walks_up():
    """_find_manifest should find a manifest in a parent directory."""
    from bootstrap import _find_manifest
    with tempfile.TemporaryDirectory() as d:
        # Project root has the manifest; deeper subdirs are searched.
        os.makedirs(os.path.join(d, "deep", "nested", "dir"))
        manifest_path = os.path.join(d, "PROJECT_MANIFEST.md")
        with open(manifest_path, "w") as f:
            f.write("repo_root: " + d + "\n")
        # Search from a deeply nested dir; should find it.
        result = _find_manifest(
            __import__("pathlib").Path(os.path.join(d, "deep", "nested", "dir"))
        )
        assert result is not None
        assert os.path.samefile(str(result), manifest_path)


def test_find_manifest_returns_none_when_missing():
    from bootstrap import _find_manifest
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        result = _find_manifest(Path(d))
        assert result is None


# ── _parse_manifest ─────────────────────────────────────────────


def test_parse_manifest_extracts_active_project():
    from bootstrap import _parse_manifest
    text = """\
# Test

repo_root: /tmp/repo
active_project: WS63 App

## Project Matrix

### WS63 App
- project_id: ws63-app
- active: true
- compile_commands: ai/projects/ws63-app/compile_commands/cdb.json
"""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        m = _parse_manifest(__import__("pathlib").Path(path))
        assert m is not None
        assert m["active_project"] == "WS63 App"
        assert len(m["projects"]) == 1
        assert m["projects"][0]["project_id"] == "ws63-app"
        assert m["projects"][0]["active"] == "true"
    finally:
        os.unlink(path)


def test_select_active_project_prefers_name_match():
    from bootstrap import _select_active_project
    manifest = {
        "active_project": "App B",
        "projects": [
            {"name": "App A", "active": "true", "project_id": "a"},
            {"name": "App B", "active": "false", "project_id": "b"},
        ],
    }
    selected = _select_active_project(manifest)
    assert selected is not None
    assert selected["project_id"] == "b"


def test_select_active_project_falls_back_to_active_true():
    from bootstrap import _select_active_project
    manifest = {
        "active_project": "",  # missing
        "projects": [
            {"name": "App A", "active": "false", "project_id": "a"},
            {"name": "App B", "active": "true", "project_id": "b"},
        ],
    }
    selected = _select_active_project(manifest)
    assert selected is not None
    assert selected["project_id"] == "b"


# ── _infer_target_from_cdb ──────────────────────────────────────


def test_infer_target_product_type_canonicalized():
    """-DPRODUCT_TYPE=3 should yield PRODUCT_3 (not PRODUCT_TYPE_3)."""
    from bootstrap import _infer_target_from_cdb
    with tempfile.TemporaryDirectory() as d:
        cdb = os.path.join(d, "cdb.json")
        with open(cdb, "w") as f:
            json.dump([
                {"command": "gcc -DPRODUCT_TYPE=3 -c a.c", "file": "a.c"},
                {"command": "gcc -DPRODUCT_TYPE=3 -c b.c", "file": "b.c"},
                {"command": "gcc -DPRODUCT_TYPE=5 -c c.c", "file": "c.c"},
            ], f)
        target = _infer_target_from_cdb(cdb)
        # PRODUCT_ wins (3 entries vs 1)
        assert target == "PRODUCT_3"


def test_infer_target_chip_macro():
    from bootstrap import _infer_target_from_cdb
    with tempfile.TemporaryDirectory() as d:
        cdb = os.path.join(d, "cdb.json")
        with open(cdb, "w") as f:
            json.dump([
                {"command": "gcc -DCHIP=WS63 -c a.c", "file": "a.c"},
            ], f)
        target = _infer_target_from_cdb(cdb)
        assert target == "CHIP_WS63"


def test_infer_target_falls_back_to_default():
    from bootstrap import _infer_target_from_cdb
    with tempfile.TemporaryDirectory() as d:
        cdb = os.path.join(d, "cdb.json")
        with open(cdb, "w") as f:
            json.dump([
                # No naming macro defined.
                {"command": "gcc -DDEBUG -DWIFI=1 -c a.c", "file": "a.c"},
            ], f)
        target = _infer_target_from_cdb(cdb)
        assert target == "DEFAULT"


def test_infer_target_handles_arguments_array():
    """Some cdb entries use 'arguments' (list) instead of 'command' (str)."""
    from bootstrap import _infer_target_from_cdb
    with tempfile.TemporaryDirectory() as d:
        cdb = os.path.join(d, "cdb.json")
        with open(cdb, "w") as f:
            json.dump([
                {"arguments": ["gcc", "-DPRODUCT_TYPE=3", "-c", "a.c"], "file": "a.c"},
            ], f)
        target = _infer_target_from_cdb(cdb)
        assert target == "PRODUCT_3"


# ── scan() ──────────────────────────────────────────────────────


def test_scan_with_initproject_manifest():
    from bootstrap import scan
    with tempfile.TemporaryDirectory() as d:
        # Set up init-project style structure.
        manifest = f"""\
repo_root: {d}
active_project: WS63 App

## Project Matrix

### WS63 App
- project_id: ws63-app
- active: true
- compile_commands: ai/projects/ws63-app/compile_commands/cdb.json
"""
        with open(os.path.join(d, "PROJECT_MANIFEST.md"), "w") as f:
            f.write(manifest)
        cdb_dir = os.path.join(d, "ai", "projects", "ws63-app", "compile_commands")
        os.makedirs(cdb_dir)
        cdb_path = os.path.join(cdb_dir, "cdb.json")
        with open(cdb_path, "w") as f:
            json.dump([
                {"command": "gcc -DPRODUCT_TYPE=3 -c a.c", "file": "a.c"},
                {"command": "gcc -DPRODUCT_TYPE=3 -c b.c", "file": "b.c"},
            ], f)

        r = scan(d)
        assert r["source"] == "init-project manifest"
        assert r["active_project"] is not None
        assert r["target"] == "PRODUCT_3"
        assert r["compile_db"] is not None
        assert r["rc_path"].endswith(
            "ai/projects/ws63-app/.macroprunerrc"
        )
        assert r["rc_already_exists"] is False
        # The compile_db in the rc is relative to repo_root.
        assert r["recommended"]["pruner.compile_db"] == (
            "ai/projects/ws63-app/compile_commands/cdb.json"
        )


def test_scan_heuristic_finds_cdb_in_build():
    from bootstrap import scan
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "build"))
        cdb = os.path.join(d, "build", "compile_commands.json")
        with open(cdb, "w") as f:
            json.dump([
                {"command": "gcc -DPRODUCT=alpha -c a.c", "file": "a.c"},
            ], f)
        r = scan(d)
        assert r["source"] == "none"  # no manifest
        assert r["target"] == "PRODUCT_alpha"
        assert r["rc_path"] == os.path.join(d, "ai", "projects", "default", ".macroprunerrc")


# ── apply() ─────────────────────────────────────────────────────


def test_apply_writes_file():
    from bootstrap import apply
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "build"))
        cdb = os.path.join(d, "build", "compile_commands.json")
        with open(cdb, "w") as f:
            json.dump([
                {"command": "gcc -DPRODUCT=beta -c a.c", "file": "a.c"},
            ], f)

        r = apply(d)
        assert r["written"] is True
        assert os.path.isfile(r["rc_path"])
        # Verify the file content is valid.
        with open(r["rc_path"]) as f:
            content = f.read()
        assert "[pruner]" in content
        assert "default_target = PRODUCT_beta" in content


def test_apply_refuses_to_overwrite():
    from bootstrap import apply
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "build"))
        cdb = os.path.join(d, "build", "compile_commands.json")
        with open(cdb, "w") as f:
            json.dump([
                {"command": "gcc -DPRODUCT=gamma -c a.c", "file": "a.c"},
            ], f)

        r1 = apply(d)
        assert r1["written"] is True
        # Pre-existing file: refused.
        r2 = apply(d)
        assert r2["written"] is False
        assert "already exists" in r2["refused_reason"]
        # With force, overwrites.
        r3 = apply(d, force=True)
        assert r3["written"] is True


# ── _format_rc ──────────────────────────────────────────────────


def test_format_rc_renders_correctly():
    from bootstrap import _format_rc
    recommended = {
        "pruner.default_target": "PRODUCT_3",
        "pruner.compile_db": "build/cdb.json",
        "pruner.default_backend": "auto",
        "pruner.path_allowlist": ["/repo"],
        "pruner.path_denylist": [".git", "node_modules"],
    }
    out = _format_rc(recommended, {})
    assert "[pruner]" in out
    assert "default_target = PRODUCT_3" in out
    assert 'compile_db = build/cdb.json' in out
    assert "default_backend = auto" in out
    assert "path_allowlist = [/repo]" in out
    assert "path_denylist = [.git, node_modules]" in out


# ── config.py integration ───────────────────────────────────────


def test_config_finds_initproject_active_rc():
    from config import _find_initproject_active_rc
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        # Set up: PROJECT_MANIFEST.md at root, active project is
        # "WS63 App" with project_id="ws63-app", and the rc is at
        # ai/projects/ws63-app/.macroprunerrc.
        manifest = f"""\
repo_root: {d}
active_project: WS63 App

## Project Matrix

### WS63 App
- project_id: ws63-app
- active: true
"""
        with open(os.path.join(d, "PROJECT_MANIFEST.md"), "w") as f:
            f.write(manifest)
        rc_dir = os.path.join(d, "ai", "projects", "ws63-app")
        os.makedirs(rc_dir)
        rc_path = os.path.join(rc_dir, ".macroprunerrc")
        with open(rc_path, "w") as f:
            f.write("default_target = PRODUCT_3\n")

        result = _find_initproject_active_rc(Path(d))
        assert result is not None
        assert os.path.samefile(str(result), rc_path)


def test_config_returns_none_when_no_manifest():
    from config import _find_initproject_active_rc
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        result = _find_initproject_active_rc(Path(d))
        assert result is None


def test_config_load_uses_initproject_rc():
    """End-to-end: load() should find the init-project rc when
    called from a nested directory."""
    from config import load
    with tempfile.TemporaryDirectory() as d:
        # Init-project structure with manifest + active rc.
        manifest = f"""\
repo_root: {d}
active_project: WS63

## Project Matrix

### WS63
- project_id: ws63
- active: true
"""
        with open(os.path.join(d, "PROJECT_MANIFEST.md"), "w") as f:
            f.write(manifest)
        rc_dir = os.path.join(d, "ai", "projects", "ws63")
        os.makedirs(rc_dir)
        rc_path = os.path.join(rc_dir, ".macroprunerrc")
        with open(rc_path, "w") as f:
            f.write("default_target = PRODUCT_3\ncompile_db = build/cdb.json\n")

        # Call load() from a nested directory; it should find
        # the rc via the manifest, not via the cwd.
        nested = os.path.join(d, "src", "lib")
        os.makedirs(nested)
        # Pass project_root to be explicit; this is how mcp_server
        # calls it.
        cfg = load(project_root=nested)
        assert cfg.get("pruner.default_target") == "PRODUCT_3"
        assert cfg.get("pruner.compile_db") == "build/cdb.json"
        assert "ws63" in cfg.get("_config_path", "")


if __name__ == "__main__":
    funcs = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    fail = 0
    for f in funcs:
        try:
            f()
            print(f"{f.__name__}: PASS")
        except AssertionError as e:
            fail += 1
            print(f"{f.__name__}: FAIL — {e}")
        except Exception as e:
            fail += 1
            print(f"{f.__name__}: ERROR — {type(e).__name__}: {e}")
    print(f"\n=== {len(funcs)-fail}/{len(funcs)} passed ===")
    sys.exit(0 if fail == 0 else 1)
