"""Benchmark-specific frontends.

Each subpackage adapts one benchmark (kernel/kGym, SWE-bench) to the
benchmark-agnostic minimization core in ``patch_minimizer.strategies``.
Core must never import from here.
"""
