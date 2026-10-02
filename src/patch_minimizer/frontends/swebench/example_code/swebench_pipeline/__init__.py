"""Reproduction pipeline steps 3-5, after minimization.

- ``postprocess``  pipeline step 3 — hidden-oracle evaluation + gold comparison
- ``consolidate``  pipeline step 4 — merge manual-assert recovery runs (optional)
- ``reconstruct``  pipeline step 5 — churn-free agent patch (feeds SCBench)

AIDEV-NOTE: No submodule imports here on purpose. ``consolidate`` mutates a results
directory when its ``main()`` runs, and importing one sibling must never drag it in.
"""
