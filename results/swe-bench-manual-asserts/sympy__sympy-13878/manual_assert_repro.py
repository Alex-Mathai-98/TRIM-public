#!/usr/bin/env python3
# In-framing: promotes the agent's own test_original_issues.py check
# ("SUCCESS: No unevaluated integrals" -- the buggy code returned unevaluated
#  Integral(...) for these CDFs; the fix adds closed-form _cdf methods) and the
# agent's test_simple_cdf.py concrete values, to hard asserts.
#
# TIMEOUT GUARD: with the fix applied each cdf(...)(pt) returns a closed form in
# <0.1s. WITHOUT the relevant _cdf method (base, or any partial-minimization
# state that drops a _cdf hunk) sympy falls back to symbolic integration of the
# pdf, which for Dagum/Frechet does not terminate in reasonable time. The
# minimizer runs this proxy once per candidate hunk removal, so an unbounded
# eval stalls the whole run. We therefore bound each eval: a timeout means the
# closed form is absent == the bug is present == the proxy must FAIL (non-zero
# exit) so the minimizer keeps the hunk.
import signal
import sys

from sympy import S
from sympy.stats import Arcsin, Dagum, Frechet, cdf

PER_EVAL_TIMEOUT_S = 4  # fix evaluates in <0.1s; 4s caps the slow partial-state
                        # symbolic-integration fallback so the minimizer (which
                        # runs this proxy once per candidate hunk removal) stays
                        # fast instead of stalling on Dagum/Frechet integration.


class _Timeout(Exception):
    pass


def _alarm(_sig, _frm):
    raise _Timeout()


signal.signal(signal.SIGALRM, _alarm)


def _eval_cdf(expr, pt, name):
    """Return str(cdf(expr)(pt)), or fail fast (exit 1) on timeout == bug present."""
    signal.alarm(PER_EVAL_TIMEOUT_S)
    try:
        return str(cdf(expr)(pt))
    except _Timeout:
        print(f"TIMEOUT: {name} cdf did not evaluate in {PER_EVAL_TIMEOUT_S}s "
              f"-> closed-form _cdf absent (bug present)")
        sys.exit(1)
    finally:
        signal.alarm(0)


# The agent's own pass/fail criterion: the CDF must NOT remain an unevaluated
# Integral. (Each added _cdf removes the Integral for that distribution.)
for name, expr, pt in [
    ("Arcsin", Arcsin("x", 0, 3), 1),
    ("Dagum", Dagum("x", S(1) / 3, S(1) / 5, 2), 3),
    ("Frechet", Frechet("x", S(4) / 3, 1, 2), 3),
]:
    result = _eval_cdf(expr, pt, name)
    assert "Integral" not in result, f"{name}: still unevaluated Integral: {result}"

# Concrete closed-form value from the agent's test_simple_cdf.py:
# Arcsin(0,1) cdf at 1/2 == (2/pi)*asin(sqrt(1/2)) == 1/2.
arcsin_half_s = _eval_cdf(Arcsin("X", 0, 1), S(1) / 2, "Arcsin(0,1)")
assert "Integral" not in arcsin_half_s, f"Arcsin(0,1) cdf still Integral: {arcsin_half_s}"
arcsin_half = cdf(Arcsin("X", 0, 1))(S(1) / 2)
assert arcsin_half == S(1) / 2, f"Arcsin(0,1) cdf(1/2) expected 1/2, got {arcsin_half}"

print("ASSERT-OK")
