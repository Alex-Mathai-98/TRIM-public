#!/usr/bin/env python3
# In-framing: promotes the agent's own test_comprehensive.py print-comparisons
# (Test 1: subbing y=1/3 makes Contains(y,[-1,1]) true so the ConditionSet
#  collapses to the imageset; Test 3: an out-of-range sub makes the condition
#  false so the result is EmptySet) to hard asserts.
from sympy import (
    symbols, Lambda, asin, pi, Rational, Contains, Interval, imageset, S,
    FiniteSet,
)
from sympy.sets import ConditionSet

x, y, n = symbols('x y n')

# Test 1 (the PR issue): condition becomes True -> imageset (base_set), not a
# self-referential ConditionSet(new, Contains(new, base), base).
imageset_expr = imageset(Lambda(n, 2 * n * pi + asin(y)), S.Integers)
condition_set = ConditionSet(x, Contains(y, Interval(-1, 1)), imageset_expr)
result = condition_set.subs(y, Rational(1, 3))
expected = imageset(Lambda(n, 2 * n * pi + asin(Rational(1, 3))), S.Integers)
assert result == expected, f"expected {expected}, got {result}"

# Test 3: condition becomes False -> EmptySet.
false_cs = ConditionSet(x, Contains(y, Interval(0, 1)), FiniteSet(1, 2, 3))
false_result = false_cs.subs(y, 2)  # 2 not in [0, 1]
assert false_result == S.EmptySet, f"expected EmptySet, got {false_result}"

print("ASSERT-OK", result)
