#!/usr/bin/env python3
# In-framing: promotes the agent's own test_pr_use_case.py print-comparisons
# (`f == s: Should be True`, `s == f: Should be True (symmetric)`,
#  `s == f2: Should be False`, both-NotImplemented => False) to hard asserts.
import sympy
from sympy import Symbol


class Foo:
    def __init__(self, coefficient):
        self._coefficient = coefficient

    def __eq__(self, other):
        if isinstance(other, sympy.Basic):
            return self._coefficient == other
        return NotImplemented

    def __repr__(self):
        return f"Foo({self._coefficient})"


x = Symbol('x')
f = Foo(x)
s = x

# Foo side already worked; the bug was the symmetric sympy side returning False.
assert (f == s) is True, f"f == s expected True, got {f == s}"
assert (s == f) is True, f"s == f expected True (symmetric), got {s == f}"

# Different value: both directions must be False.
y = Symbol('y')
f2 = Foo(y)
assert (f2 == s) is False, f"f2 == s expected False, got {f2 == s}"
assert (s == f2) is False, f"s == f2 expected False, got {s == f2}"

# Both return NotImplemented => Python falls back to identity => False.
class AlwaysNotImplemented:
    def __eq__(self, other):
        return NotImplemented

ani = AlwaysNotImplemented()
assert (s == ani) is False, f"s == ani expected False, got {s == ani}"


# In-framing from the agent's test_inequality.py: __ne__ must also delegate
# symmetrically (a custom class with NotImplemented-aware __ne__).
class Bar:
    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        if isinstance(other, sympy.Basic):
            return self.value == other
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        if result is NotImplemented:
            return NotImplemented
        return not result

    def __repr__(self):
        return f"Bar({self.value})"


b1 = Bar(x)
b2 = Bar(y)
# matching value: != must be False symmetrically
assert (b1 != x) is False, f"b1 != x expected False, got {b1 != x}"
assert (x != b1) is False, f"x != b1 expected False (symmetric), got {x != b1}"
# non-matching value: != must be True symmetrically
assert (b2 != x) is True, f"b2 != x expected True, got {b2 != x}"
assert (x != b2) is True, f"x != b2 expected True (symmetric), got {x != b2}"

# In-framing from the agent's test_edge_cases.py: Lambda.__eq__ vs non-Lambda
# must not collapse a symmetric custom-class comparison to plain False.
from sympy import Lambda

lam = Lambda(x, x**2)
fl = Foo(lam)
assert (fl == lam) is True, f"fl == lam expected True, got {fl == lam}"
assert (lam == fl) is True, f"lam == fl expected True (symmetric), got {lam == fl}"

# In-framing from the agent's test_comprehensive_comparison.py: the symmetric
# fix must also hold for Number subclasses (Integer/Rational/Float), which have
# their own __eq__ that previously returned False on un-sympifiable rhs.
from sympy import Integer, Rational, Float

for sym_val in (Integer(5), Rational(1, 2), Float(3.14)):
    fnum = Foo(sym_val)
    # custom-class side already worked; sympy-Number side was the broken one.
    assert (fnum == sym_val) is True, f"fnum == {sym_val} expected True, got {fnum == sym_val}"
    assert (sym_val == fnum) is True, (
        f"{sym_val} == fnum expected True (symmetric), got {sym_val == fnum}"
    )
    # non-matching custom value: both directions False.
    fnum_bad = Foo(Symbol('zzz'))
    assert (sym_val == fnum_bad) is False, (
        f"{sym_val} == fnum_bad expected False, got {sym_val == fnum_bad}"
    )

# In-framing from the agent's test_inequality.py + test_pr_use_case.py: the
# __ne__ delegation must be symmetric for *every* type whose __eq__ was changed
# to return NotImplemented (Lambda, Subs, Number subclasses). A NotImplemented-
# aware custom __ne__ (Bar) compared to these must yield the right bool, which is
# only possible if their __ne__ propagates NotImplemented instead of `not False`.
from sympy.core.function import Subs

subs_obj = Subs(x**2, x, x)


def check_ne_symmetry(sym_obj, label):
    b_match = Bar(sym_obj)
    b_diff = Bar(Symbol('qqq'))
    # matching: != False in both directions
    assert (b_match != sym_obj) is False, f"{label}: b_match != obj expected False, got {b_match != sym_obj}"
    assert (sym_obj != b_match) is False, f"{label}: obj != b_match expected False (symmetric), got {sym_obj != b_match}"
    # non-matching: != True in both directions
    assert (b_diff != sym_obj) is True, f"{label}: b_diff != obj expected True, got {b_diff != sym_obj}"
    assert (sym_obj != b_diff) is True, f"{label}: obj != b_diff expected True (symmetric), got {sym_obj != b_diff}"


check_ne_symmetry(lam, "Lambda")
check_ne_symmetry(subs_obj, "Subs")
for sv in (Integer(5), Rational(1, 2), Float(3.14)):
    check_ne_symmetry(sv, type(sv).__name__)

print("ASSERT-OK")
