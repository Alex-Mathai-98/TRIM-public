#!/usr/bin/env python3
# In-framing: promotes the agent's own debug_issue.py inspection of
# `routine.arguments` and their `dimensions` (it printed each arg's dimensions)
# to a hard assert -- WITHOUT compiling (no cython/C-compiler needed). The bug:
# a MatrixSymbol passed in argument_sequence but NOT appearing in the expression
# gets no `dimensions` metadata, so codegen emits a wrong C signature. The fix
# populates array_symbols from argument_sequence. The minimizer over-dropped that
# population hunk; we assert the MatrixSymbol arg receives its dimensions.
import sys

from sympy import MatrixSymbol, S
from sympy.utilities.codegen import get_code_generator

# expr = 1.0 does NOT depend on the MatrixSymbol x, but x is an explicit arg.
x = MatrixSymbol('x', 2, 1)
code_gen = get_code_generator('C99', "autowrap")
routine = code_gen.routine('autofunc', S(1.0), (x,))

args = {str(a.name): a for a in routine.arguments}
assert 'x' in args, "MatrixSymbol arg 'x' missing from routine args: %s" % list(args)

dims = getattr(args['x'], 'dimensions', None)
assert dims is not None, "MatrixSymbol arg 'x' has no dimensions (bug present)"
dims = list(dims)
assert len(dims) == 2, "x dimensions wrong arity: %r" % (dims,)
# shape (2, 1) -> [(0, 1), (0, 0)]
assert [tuple(d) for d in dims] == [(S.Zero, S(1)), (S.Zero, S.Zero)], \
    "x dimensions value wrong: %r" % (dims,)

print("ASSERT-OK", dims)
