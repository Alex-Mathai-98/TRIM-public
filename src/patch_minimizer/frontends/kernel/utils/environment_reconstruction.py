"""Reconstruct a Kernel_Agent Environment from a saved snapshot.

AIDEV-NOTE: Requires the Kernel_Agent package (EnvironmentSnapshot, LiteLLMRouter).
Only used by coupling-analysis / MinimizationAgent integration paths, not by
standalone minimization.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from patch_minimizer.core.rw_lock import RWLock


def reconstruct_environment(folder_path, model_id, bug_id, linux_dir, code_dir_lock,
                            benchmark_folder=None, minimization_only=False):
    """Reconstruct lightweight Environment from EnvironmentSnapshot.

    AIDEV-NOTE: Extracted from minimize_patches.py. The env is used by
    MinimizationAgent for both coupling analysis and future option-1 integration.
    When minimization_only=True and the snapshot is missing, falls back to
    reconstructing from bug JSON + config.json (for legacy runs).
    """
    from Kernel_Agent.environment.environment_snapshot import EnvironmentSnapshot
    from Kernel_Agent.kgym_utils.util_funcs import instantiate_llm_response_history
    from Kernel_Agent.models.litellm_router import LiteLLMRouter

    logger = logging.getLogger(f"minimization_agent_{bug_id}")
    logger.setLevel(logging.INFO)

    try:
        snapshot = EnvironmentSnapshot.load(folder_path, model_id, bug_id)
    except FileNotFoundError:
        if not minimization_only or benchmark_folder is None:
            raise  # Fresh run — missing snapshot is a real bug, don't swallow it
        config_path = Path(folder_path).parent / "config.json"
        with open(config_path) as f:
            config = json.load(f)
        from patch_minimizer.benchmark_utils.kernel.bug_data import BugData
        bug_data = BugData.from_json_file(os.path.join(benchmark_folder, f"{bug_id}.json"))
        snapshot = EnvironmentSnapshot.from_bug_data(bug_data, model_id, folder_path, config, logger)
        snapshot.save(save_dir=folder_path)
    llm_response_history = instantiate_llm_response_history(
        save_dir=folder_path, base_commit=snapshot.base_commit,
        bug_id=bug_id, model_id=model_id,
    )
    response_history_lock = RWLock()

    return snapshot.reconstruct(
        code_dir=linux_dir,
        code_dir_lock=code_dir_lock,
        logger=logger,
        response_history=llm_response_history,
        response_history_lock=response_history_lock,
        llm_model=LiteLLMRouter(model_id=model_id, locations=["us-east5"]),
    )
