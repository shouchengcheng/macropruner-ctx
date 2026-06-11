"""Tests for the .macroprunerrc loader and resolver."""
import os
import tempfile
import textwrap

from config import load, resolve_compile_db, DEFAULTS


def _write(path, content):
    with open(path, "w") as f:
        f.write(textwrap.dedent(content))


def test_defaults_when_no_config():
    with tempfile.TemporaryDirectory() as d:
        c = load(project_root=d)
        assert c["pruner.default_target"] == ""
        assert c["pruner.compile_db"] == ""
        assert c["pruner.default_backend"] == "regex"
        assert c["pruner.default_mode"] == "physical"
        assert c["_config_path"] == ""


def test_basic_kv_parsing():
    with tempfile.TemporaryDirectory() as d:
        _write(
            os.path.join(d, ".macroprunerrc"),
            """
            # my comment
            default_target = PRODUCT_A
            default_backend = auto
            default_mode = virtual
            token_budget = 8000
            """,
        )
        c = load(project_root=d)
        assert c["pruner.default_target"] == "PRODUCT_A"
        assert c["pruner.default_backend"] == "auto"
        assert c["pruner.default_mode"] == "virtual"
        assert c["pruner.token_budget"] == 8000
        # comment-only and blank lines tolerated


def test_section_syntax_accepted():
    with tempfile.TemporaryDirectory() as d:
        _write(
            os.path.join(d, ".macroprunerrc"),
            """
            [pruner]
            default_target = X
            [deps]
            default_max_depth = 4
            """,
        )
        c = load(project_root=d)
        assert c["pruner.default_target"] == "X"
        # Unknown section goes to _extra; the tool only acts on
        # whitelisted keys in DEFAULTS.
        assert c["_extra"]["deps.default_max_depth"] == 4


def test_coercion_bool_and_numbers():
    with tempfile.TemporaryDirectory() as d:
        _write(
            os.path.join(d, ".macroprunerrc"),
            """
            token_budget = 12000
            default_max_depth = 3
            """,
        )
        c = load(project_root=d)
        assert c["pruner.token_budget"] == 12000
        assert isinstance(c["pruner.token_budget"], int)
        assert c["pruner.default_max_depth"] == 3


def test_coercion_list():
    with tempfile.TemporaryDirectory() as d:
        _write(
            os.path.join(d, ".macroprunerrc"),
            """
            include_dirs = [inc, /abs/inc, "with space"]
            """,
        )
        c = load(project_root=d)
        assert c["pruner.include_dirs"] == ["inc", "/abs/inc", "with space"]


def test_extra_keys_kept_in_extra():
    with tempfile.TemporaryDirectory() as d:
        _write(
            os.path.join(d, ".macroprunerrc"),
            """
            [experimental]
            my_custom_future_key = whatever
            """,
        )
        c = load(project_root=d)
        # Section prefix is preserved; unknown sections go to _extra.
        assert c["_extra"]["experimental.my_custom_future_key"] == "whatever"


def test_resolve_compile_db_relative():
    with tempfile.TemporaryDirectory() as d:
        _write(
            os.path.join(d, "compile_commands.json"),
            "[]",  # content doesn't matter
        )
        c = {"pruner.compile_db": "compile_commands.json"}
        result = resolve_compile_db(c, project_root=d)
        assert result == os.path.join(d, "compile_commands.json")


def test_resolve_compile_db_fallback_to_build_dir():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "build"))
        _write(
            os.path.join(d, "build", "compile_commands.json"),
            "[]",
        )
        c = {"pruner.compile_db": ""}  # empty
        result = resolve_compile_db(c, project_root=d)
        assert result == os.path.join(d, "build", "compile_commands.json")


def test_resolve_compile_db_returns_none_when_missing():
    with tempfile.TemporaryDirectory() as d:
        c = {"pruner.compile_db": ""}
        result = resolve_compile_db(c, project_root=d)
        assert result is None


def test_malformed_config_falls_back_to_defaults():
    with tempfile.TemporaryDirectory() as d:
        # File with binary garbage that breaks UTF-8 reading
        path = os.path.join(d, ".macroprunerrc")
        with open(path, "wb") as f:
            f.write(b"\xff\xfe garbage")
        c = load(project_root=d)
        # Should not crash; should still have defaults.
        assert c["pruner.default_backend"] == "regex"
        assert "_config_error" in c


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
