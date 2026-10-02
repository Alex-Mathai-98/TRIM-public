#!/usr/bin/env python3
# In-framing: promotes the agent's own test_pr_example.py print-comparison
# (`expected = sqrt(5)`, `result == expected`) to a hard assert.
from sympy.geometry import Point
from sympy import sqrt

result = Point(2, 0).distance(Point(1, 0, 2))
expected = sqrt(5)
assert result == expected, f"expected {expected}, got {result}"
print("ASSERT-OK", result)
