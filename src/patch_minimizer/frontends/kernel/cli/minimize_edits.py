#!/usr/bin/env python3
"""Kernel minimize_edits() — bring-your-own patch_list entry point.

AIDEV-NOTE: This module provides minimize_edits() for callers that already have
a patch_list (e.g. parsed from a trajectory file via an adapter). For the
.pkl-based CrashFixer path, see sol_minimize.py (ToolSolnMinimize,
minimize_bug_solution, run_minimization_on_results).
"""
from __future__ import annotations

import os
import json
import logging
from pathlib import Path
from typing import List, Tuple

from patch_minimizer.core.edit import Edit
from patch_minimizer.core.rw_lock import RWLock, GitRWLock
from patch_minimizer.strategies.minimize_core import (
    minimize_edit_list,
    save_minimization_results,
    serialize_edit_path,
)


# AIDEV-NOTE: Standalone entry point for minimization — accepts patch_list directly,
# bypassing CrashFixer-specific data loading (.pkl, LLMResponseHistory, SolutionPathAnalyzer).
def minimize_edits(
    bug_id: str,
    patch_list: List[Tuple[str | None, List[Edit]]],
    base_commit: str,
    linux_dir: str,
    code_dir_lock: RWLock,
    benchmark_folder: str,
    golden_subset_data: dict,
    kernel_base_url: str | None = None,
    run_jobs: bool = False,
    syzkaller_rollback_tag: str | None = None,
    minimize_at: str = "edit",
    coupling_map=None,
    save_dir: str | None = None,
    use_result_cache: bool = True,
) -> dict:
    """Standalone kernel minimization — bring your own patch_list.

    AIDEV-NOTE: Kernel/dry-run only. SWE-bench builds its own environment and calls
    ``minimize_core.minimize_edit_list`` directly — see frontends/swebench/.

    Args:
        bug_id: Bug identifier (for logging and KernelEnvironment).
        patch_list: The edits to minimize. Each tuple is (parent_diff, List[Edit]).
            parent_diff is an optional git diff applied before the edits in that node.
            Each Edit must have a unique global_edit_idx across the entire patch_list.
        base_commit: Git commit hash to reset the repo to.
        linux_dir: Path to a Linux repository.
        code_dir_lock: RWLock for thread-safe repo access.
        benchmark_folder: Path to benchmark folder (for KernelEnvironment).
        golden_subset_data: Golden subset data (for KernelEnvironment kernel cache/image).
        kernel_base_url: Base URL for kernel git repo (optional).
        run_jobs: If True, use KernelEnvironment (real kernel builds); else DryRunEnvironment.
        syzkaller_rollback_tag: Syzkaller rollback tag (optional).
        minimize_at: "node" or "edit" granularity.
        coupling_map: Pre-computed CouplingMap (optional).
        save_dir: Directory to save results JSON (optional).

    Returns:
        dict with short_path, minimized_patch, feedback_stats, original_edit_count,
        minimized_edit_count, reduction.
    """
    from patch_minimizer.strategies.algorithms.node.node_minimizer import NodeMinimizer
    from patch_minimizer.strategies.algorithms.edit.edit_minimizer import EditMinimizer
    from patch_minimizer.strategies.environments import (
        DryRunEnvironment, FeedbackReceiver, GitDiffEnvironment,
    )
    from patch_minimizer.frontends.kernel.environment import KernelEnvironment

    logger = logging.getLogger(f"minimize_edits_{bug_id}")
    logger.setLevel(logging.INFO)

    minimizer_cls = NodeMinimizer if minimize_at == "node" else EditMinimizer
    smb = minimizer_cls(
        linux_dir, code_dir_lock, base_commit, logger,
        kernel_base_url=kernel_base_url, syzkaller_rollback_tag=syzkaller_rollback_tag,
        use_result_cache=use_result_cache,
    )

    if run_jobs:
        runtime_env = KernelEnvironment(
            benchmark_folder,
            bug_id,
            golden_subset_data,
            linux_dir,
            logger,
            syzkaller_rollback_tag,
        )
    else:
        runtime_env = DryRunEnvironment()
    feedback_aggregator = FeedbackReceiver(runtime_env, git_diff_env=GitDiffEnvironment())

    result = minimize_edit_list(
        bug_id=bug_id,
        patch_list=patch_list,
        minimizer=smb,
        feedback_aggregator=feedback_aggregator,
        minimize_at=minimize_at,
        coupling_map=coupling_map,
        save_dir=save_dir,
    )

    if save_dir:
        save_minimization_results(result, patch_list, save_dir)

    return result
