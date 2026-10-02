"""CrashFixer (kernel agent) trajectory adapter.

Converts saved .pkl files (LLMResponseHistory decision trees) into the
canonical patch_list format for minimization.
"""
from patch_minimizer.agent_adapters.kagent_adapter.adapter import (
    load_all_trajectories,
    load_trajectory,
    count_trajectories,
)

__all__ = [
    "load_all_trajectories",
    "load_trajectory",
    "count_trajectories",
]
