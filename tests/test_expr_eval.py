"""Smoke test for ExpressionEvaluator. Run as: .venv/bin/python test_expr_eval.py"""
from expr_eval import ExpressionEvaluator

cases = [
    ({'PRODUCT_TYPE': '3'}, 'PRODUCT_TYPE == 3', True),
    ({'PRODUCT_TYPE': '3'}, 'PRODUCT_TYPE == 5', False),
    ({'PRODUCT_TYPE': '3'}, 'PRODUCT_TYPE != 5', True),
    ({'A': None, 'B': None}, 'defined(A) && defined(B)', True),
    ({'A': None}, 'defined(A) && defined(B)', False),
    ({'A': None, 'B': None}, 'defined(A) || defined(B)', True),
    ({'A': None}, '!defined(A)', False),
    ({'A': None}, '!defined(A) || defined(B)', False),  # both false
    ({'A': None, 'B': None}, '!defined(A) || defined(B)', True),
    ({'CPU_ARM': '1'}, 'CPU_ARM', True),  # bare defined-with-value → 1
    ({'CPU_ARM': '0'}, 'CPU_ARM', False),  # bare defined-with-value 0 → 0
    ({'X': '5'}, 'X > 3 && X < 10', True),
    ({'X': '5'}, 'X >= 5 && X <= 5', True),
    ({'X': '5'}, '(X == 5) || (X == 6)', True),
    ({'X': '5'}, '(X == 5) && (X == 6)', False),
    ({'X': '0xa'}, 'X == 10', True),  # hex
    ({'X': '5'}, 'X + 1 == 6', True),
    ({'X': '5'}, 'X * 2 == 10', True),
    ({'X': None}, 'X == 1', True),  # defined-no-value → 1
    ({'X': None}, 'X == 0', False),  # defined-no-value → 1, not 0
    ({}, 'A == 1', False),  # undefined identifier in compare → 0
    ({'A': None, 'B': None}, 'IS_ENABLED(A) || IS_ENABLED(B)', True),
    ({'A': None}, 'IS_ENABLED(B)', False),
    ({'X': '2', 'Y': '3'}, 'X + Y == 5', True),
    ({'X': '0', 'Y': '5'}, '!X && Y', True),  # 0 is falsy
    ({'X': '0'}, '!X', True),
    ({'X': '1'}, '!X', False),
    ({'X': '1', 'Y': '1'}, 'X == 1 && Y == 1', True),
]

fail = 0
for macros, cond, expected in cases:
    try:
        result = ExpressionEvaluator(macros).evaluate(cond)
        ok = result == expected
    except Exception as e:
        ok = False
        result = f"raised {type(e).__name__}: {e}"
    marker = "OK  " if ok else "FAIL"
    if not ok:
        fail += 1
    print(f"{marker}  {macros} | {cond!r} => {result} (want {expected})")

print(f"\n=== {len(cases)-fail}/{len(cases)} passed ===")
