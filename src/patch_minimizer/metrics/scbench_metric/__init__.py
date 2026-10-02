"""SCBench (SlopCodeBench) alternate code-slop metric integration.

Public API:
    SCBenchMetricRunner  - run the SCBench slop metric on a directory
    SWEBenchRepoProvider - clean base-commit checkout from a SWE-bench image
    evaluate_patch       - clean -> apply patch -> measure -> reset
    evaluate_pair        - before/after/reduction for agent vs. minimized patch
"""
from patch_minimizer.metrics.scbench_metric.scbench_runner import (
    SCBenchError,
    SCBenchMetricRunner,
    SWEBenchRepoProvider,
    changed_files,
    evaluate_pair,
    evaluate_patch,
    new_files,
)

__all__ = [
    "SCBenchError",
    "SCBenchMetricRunner",
    "SWEBenchRepoProvider",
    "changed_files",
    "evaluate_pair",
    "evaluate_patch",
    "new_files",
]
