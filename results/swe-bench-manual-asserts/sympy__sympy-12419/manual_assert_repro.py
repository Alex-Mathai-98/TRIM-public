#!/usr/bin/env python3
# In-framing: promotes the agent's own reproduce_issue.py print-comparison
# (`total_sum = Sum(Sum(e[i,j],...),...).doit()`, `Expected: n`,
#  `Test passed: total_sum == n`) to a hard assert.
from sympy import Symbol, symbols, MatrixSymbol, refine, Sum
from sympy import Q as Query
from sympy.assumptions import assuming

n = Symbol('n', integer=True, positive=True)
i, j = symbols('i j', integer=True)
M = MatrixSymbol('M', n, n)

with assuming(Query.orthogonal(M)):
    e = refine((M.T * M).doit())

# Total sum of all elements of the (orthogonal) identity must be n, not 0.
total_sum = Sum(Sum(e[i, j], (i, 0, n - 1)), (j, 0, n - 1)).doit()
expected = n
assert total_sum == expected, f"expected {expected}, got {total_sum}"
print("ASSERT-OK", total_sum)
