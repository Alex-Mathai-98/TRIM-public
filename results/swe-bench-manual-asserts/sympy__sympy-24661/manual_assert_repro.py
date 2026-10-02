#!/usr/bin/env python3
# In-framing: promotes the agent's own final_test_fixed.py print-comparisons
# (parse_expr('1 < 2', evaluate=False) must equal Lt(1, 2, evaluate=False) -- an
#  unevaluated StrictLessThan -- instead of the buggy eagerly-evaluated `True`;
#  same for all six relational operators) to hard asserts.
from sympy.parsing.sympy_parser import parse_expr
from sympy import Lt, Gt, Le, Ge, Eq, Ne
from sympy.core.relational import (
    StrictLessThan, StrictGreaterThan, LessThan, GreaterThan,
    Equality, Unequality,
)

# PR issue: must stay unevaluated and match the explicit Lt(..., evaluate=False).
result = parse_expr('1 < 2', evaluate=False)
expected = Lt(1, 2, evaluate=False)
assert type(result) == type(expected), f"type mismatch: {type(result)} vs {type(expected)}"
assert result == expected, f"expected {expected}, got {result}"

# All six operators must produce the corresponding unevaluated relational type
# (the agent's final_test_fixed.py case list).
cases = [
    ('1 < 2', StrictLessThan),
    ('1 > 2', StrictGreaterThan),
    ('1 <= 2', LessThan),
    ('1 >= 2', GreaterThan),
    ('1 == 2', Equality),
    ('1 != 2', Unequality),
]
for expr, expected_type in cases:
    r = parse_expr(expr, evaluate=False)
    assert isinstance(r, expected_type), f"'{expr}' expected {expected_type.__name__}, got {type(r).__name__}: {r}"

print("ASSERT-OK", result)
