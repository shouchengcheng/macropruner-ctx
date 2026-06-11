"""Tests for the CompileDBParser mtime-based cache."""
import json
import os
import tempfile
import time

from cc_parser import CompileDBParser, _cache_get, clear_cache


def _setup_db(tmpdir, content=None):
    if content is None:
        content = [
            {
                "directory": tmpdir,
                "command": "gcc -DPRODUCT_A -c main.c -o main.o",
                "file": "main.c",
            }
        ]
    path = os.path.join(tmpdir, "compile_commands.json")
    with open(path, "w") as f:
        json.dump(content, f)
    return path


def test_cache_hit_on_repeat_load():
    with tempfile.TemporaryDirectory() as d:
        db = _setup_db(d)
        clear_cache()
        p1 = CompileDBParser(db)
        e1 = p1._load()
        # Second parser on same path should hit the cache.
        p2 = CompileDBParser(db)
        e2 = p2._load()
        assert e1 is e2, "Second load should return cached list object"


def test_cache_invalidates_on_mtime_change():
    with tempfile.TemporaryDirectory() as d:
        db = _setup_db(d)
        clear_cache()
        p1 = CompileDBParser(db)
        e1 = p1._load()
        # Sleep so mtime is guaranteed to differ.
        time.sleep(0.05)
        # Rewrite the file with new content.
        with open(db, "w") as f:
            json.dump([{
                "directory": d,
                "command": "gcc -DPRODUCT_B -c main.c -o main.o",
                "file": "main.c",
            }], f)
        # Force mtime to be newer (some filesystems have second resolution).
        os.utime(db, None)
        p2 = CompileDBParser(db)
        e2 = p2._load()
        # Should NOT be the same list (cache invalidated).
        assert e1 is not e2 or e1 != e2
        # And new content is in effect.
        macros = p2.extract_macros(os.path.join(d, "main.c"))
        assert "PRODUCT_B" in macros
        assert "PRODUCT_A" not in macros


def test_clear_cache_drops_entries():
    with tempfile.TemporaryDirectory() as d:
        db = _setup_db(d)
        clear_cache()
        p = CompileDBParser(db)
        p._load()
        assert _cache_get(db) is not None
        clear_cache()
        assert _cache_get(db) is None


def test_cache_handles_missing_file():
    with tempfile.TemporaryDirectory() as d:
        db = _setup_db(d)
        clear_cache()
        p = CompileDBParser(db)
        p._load()
        # Delete the file out from under the parser.
        os.unlink(db)
        # Should not crash; should drop the cache entry.
        result = _cache_get(db)
        assert result is None


def test_cache_max_entries_evicts_oldest():
    """Filling the cache past CACHE_MAX_ENTRIES evicts the oldest."""
    from cc_parser import _CACHE, CACHE_MAX_ENTRIES
    with tempfile.TemporaryDirectory() as d:
        clear_cache()
        # Create MORE than CACHE_MAX_ENTRIES different db files.
        paths = []
        for i in range(CACHE_MAX_ENTRIES + 2):
            sub = os.path.join(d, f"p{i}")
            os.makedirs(sub, exist_ok=True)
            db = _setup_db(sub)
            paths.append(db)
            CompileDBParser(db)._load()
        # Cache should be at most CACHE_MAX_ENTRIES.
        assert len(_CACHE) <= CACHE_MAX_ENTRIES
        # The very first inserted should be gone.
        assert paths[0] not in _CACHE


if __name__ == "__main__":
    import sys
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
