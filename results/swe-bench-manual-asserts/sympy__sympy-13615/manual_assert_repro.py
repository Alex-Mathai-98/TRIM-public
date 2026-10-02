#!/usr/bin/env python3
# In-framing: promotes the agent's own test_final_verification.py print-checks
# (`Complement(FiniteSet(x,y,2), Interval(-10,10))` must drop the contained
#  number 2 and keep a Complement of FiniteSet({x,y}) with the interval;
#  `2 in result` -> False) to hard asserts.
from sympy import symbols, FiniteSet, Interval, Complement

x, y = symbols('x y')

a = FiniteSet(x, y, 2)
b = Interval(-10, 10)
result = Complement(a, b)

# The buggy base collapsed this to {x, y} (or kept 2); the fix must yield a
# Complement(FiniteSet(x, y), Interval(-10, 10)) -- 2 removed, structure kept.
assert isinstance(result, Complement), f"expected Complement, got {type(result)}: {result}"
first_arg, second_arg = result.args
assert isinstance(first_arg, FiniteSet), f"first arg not FiniteSet: {first_arg}"
assert set(first_arg.args) == {x, y}, f"expected {{x, y}}, got {set(first_arg.args)}"
assert second_arg == b, f"expected interval {b}, got {second_arg}"

# 2 was inside the interval, so it must NOT be in the complement.
assert (2 in result) is False, f"2 in result expected False, got {2 in result}"

# In-framing from the agent's test_comprehensive.py: the numeric-only and
# symbol-only branches must also behave, otherwise the fix is incomplete.
from sympy import EmptySet

# only numbers, all inside -> EmptySet
r_all_in = Complement(FiniteSet(1, 2, 3), Interval(-10, 10))
assert r_all_in == EmptySet(), f"all-inside expected EmptySet(), got {r_all_in}"

# only numbers, some outside -> {15, 20}
r_some_out = Complement(FiniteSet(1, 2, 15, 20), Interval(-10, 10))
assert r_some_out == FiniteSet(15, 20), f"some-outside expected {{15, 20}}, got {r_some_out}"

print("ASSERT-OK", result)
