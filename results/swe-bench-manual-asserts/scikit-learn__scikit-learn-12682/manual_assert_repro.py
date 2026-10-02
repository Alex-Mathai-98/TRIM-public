#!/usr/bin/env python3
"""Manual assert derived from the agent's own test_fix.py. The agent verified
that SparseCoder now accepts a transform_max_iter parameter, stores it
(coder.transform_max_iter == 50), and that a very low max_iter propagates into
the lasso_cd transform (producing a ConvergenceWarning). With the bug, passing
transform_max_iter raises TypeError. We run construction WITHOUT try/except so
the bug yields a nonzero exit, then assert the agent's checked values."""

import warnings
import numpy as np
from sklearn.decomposition import SparseCoder
from sklearn.exceptions import ConvergenceWarning

np.random.seed(42)
n_samples, n_features, n_components = 10, 20, 5
dictionary = np.random.randn(n_components, n_features)
dictionary = dictionary / np.linalg.norm(dictionary, axis=1, keepdims=True)
X = np.random.randn(n_samples, n_features)

# Bug: transform_max_iter not a valid kwarg -> TypeError (nonzero exit).
coder = SparseCoder(
    dictionary=dictionary,
    transform_algorithm="lasso_cd",
    transform_alpha=0.1,
    transform_max_iter=50,
)
print(f"transform_max_iter stored: {coder.transform_max_iter}")
assert coder.transform_max_iter == 50, "transform_max_iter not stored"

# Low max_iter must actually reach lasso_cd -> ConvergenceWarning.
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    coder_low = SparseCoder(
        dictionary=dictionary,
        transform_algorithm="lasso_cd",
        transform_alpha=0.001,
        transform_max_iter=1,
    )
    coder_low.transform(X)
    conv = [x for x in w if issubclass(x.category, ConvergenceWarning)]
    print(f"ConvergenceWarnings with max_iter=1: {len(conv)}")
    assert conv, "max_iter=1 did not propagate to lasso_cd (no ConvergenceWarning)"

print("ASSERT-OK")
