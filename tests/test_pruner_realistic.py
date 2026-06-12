"""Verify PrunerCore now handles the real-world patterns that
the old evaluate_condition could not. Each test is a full #if/endif
block being correctly evaluated as active or inactive."""
from pruner_core import PrunerCore, PrunerMode


def run(label, source, macros, want_active_count, want_removed_count):
    p = PrunerCore(macros, PrunerMode.PHYSICAL_DELETION)
    pruned = p.prune(source)
    active = "init_a()" if want_active_count == 1 else "init_b()"
    inactive = "init_b()" if want_active_count == 1 else "init_a()"
    ok = (active in pruned) and (inactive not in pruned)
    print(
        f"{'OK  ' if ok else 'FAIL'} {label}: "
        f"active={'in' if active in pruned else 'out'} "
        f"inactive={'in' if inactive in pruned else 'out'}"
    )
    return ok


# (label, source, macros, want_active_side)
cases = [
    (
        "#if MACRO == N",
        "#if PRODUCT_TYPE == 3\nvoid init_a() {}\n#else\nvoid init_b() {}\n#endif",
        {"PRODUCT_TYPE": "3"},
        "a",
    ),
    (
        "#if MACRO != N",
        "#if PRODUCT_TYPE != 1\nvoid init_a() {}\n#else\nvoid init_b() {}\n#endif",
        {"PRODUCT_TYPE": "2"},
        "a",
    ),
    (
        "#if defined(A) && defined(B)",
        "#if defined(HAS_WIFI) && defined(HAS_BLE)\nvoid init_a() {}\n#else\nvoid init_b() {}\n#endif",
        {"HAS_WIFI": None, "HAS_BLE": None},
        "a",
    ),
    (
        "#if defined(A) || defined(B)",
        "#if defined(HAS_WIFI) || defined(HAS_BLE)\nvoid init_a() {}\n#else\nvoid init_b() {}\n#endif",
        {"HAS_BLE": None},
        "a",
    ),
    (
        "IS_ENABLED(CONFIG_X)",
        "#if IS_ENABLED(CONFIG_WIFI)\nvoid init_a() {}\n#else\nvoid init_b() {}\n#endif",
        {"CONFIG_WIFI": None},
        "a",
    ),
    (
        "case-insensitive CPU_ARM == 1",
        "#if CPU_ARM == 1\nvoid init_a() {}\n#else\nvoid init_b() {}\n#endif",
        {"cpu_arm": "1"},
        "a",
    ),
    (
        "#if VALUE (bare)",
        "#if ZERO\nvoid init_a() {}\n#else\nvoid init_b() {}\n#endif",
        {"ZERO": "0"},
        "b",  # 0 is falsy → init_b
    ),
    (
        "nested #if (outer true, INNER undefined → init_b)",
        "#if OUTER\n#  if defined(INNER)\nvoid init_a() {}\n#  else\nvoid init_b() {}\n#  endif\n#else\nvoid init_c() {}\n#endif",
        {"OUTER": "1"},
        "b",
    ),
    (
        "nested #if (outer true, inner true → init_a)",
        "#if OUTER\n#  if defined(INNER)\nvoid init_a() {}\n#  else\nvoid init_b() {}\n#  endif\n#else\nvoid init_c() {}\n#endif",
        {"OUTER": "1", "INNER": None},
        "a",
    ),
    (
        "nested #if (outer false → init_c)",
        "#if OUTER\n#  if defined(INNER)\nvoid init_a() {}\n#  else\nvoid init_b() {}\n#  endif\n#else\nvoid init_c() {}\n#endif",
        {},
        "c",
    ),
]

fail = 0
for label, src, macros, want in cases:
    p = PrunerCore(macros, PrunerMode.PHYSICAL_DELETION)
    pruned = p.prune(src)
    expected = f"init_{want}()"
    excluded = [f"init_{x}()" for x in "abc" if x != want]
    ok = expected in pruned and all(x not in pruned for x in excluded)
    if not ok:
        fail += 1
    print(
        f"{'OK  ' if ok else 'FAIL'} {label}: "
        f"want init_{want}(); got: {pruned.strip()[:80]!r}"
    )

print(f"\n=== {len(cases)-fail}/{len(cases)} passed ===")
