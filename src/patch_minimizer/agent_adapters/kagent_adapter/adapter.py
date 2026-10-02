"""CrashFixer (kernel agent) trajectory adapter.

Converts a saved CrashFixer .pkl (LLMResponseHistory decision tree) into the
canonical patch_list format used by the minimization pipeline.

Unlike the JSON-based adapters that go through TrajectoryParser + parser_registry,
this adapter handles binary .pkl files containing a networkx DiGraph — a
fundamentally different format that doesn't fit the registry dispatch model.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from patch_minimizer.benchmark_utils.kernel.util_funcs import instantiate_llm_response_history
from patch_minimizer.core.edit import Edit

from .solution_path_analyzer import SolutionPathAnalyzer

# AIDEV-NOTE: Type aliases matching the rest of the codebase.
# patch_list: one trajectory's edits — List[(parent_git_diff | None, List[Edit])]
PatchList = List[Tuple[Optional[str], List[Edit]]]


def load_all_trajectories(
    folder_path: str,
    bug_id: str,
    model_id: str,
    base_commit: str,
) -> tuple[list[PatchList], list[list[str]]]:
    """Parse a CrashFixer .pkl into all successful trajectories.

    Args:
        folder_path: Directory containing the .pkl file.
        bug_id: Kernel bug identifier.
        model_id: Model identifier (used to construct the .pkl filename).
        base_commit: Git commit SHA the agent ran against.

    Returns:
        (succ_edit_paths, trajectory_names) where:
        - succ_edit_paths[i] is a patch_list for the i-th successful trajectory
        - trajectory_names[i] is the list of node-name strings for that trajectory

    Raises:
        AssertionError: If the .pkl file is missing or its metadata doesn't match.
    """
    llm_response_history = instantiate_llm_response_history(
        save_dir=folder_path,
        base_commit=base_commit,
        bug_id=bug_id,
        model_id=model_id,
    )

    succ_edit_paths, trajectory_names = SolutionPathAnalyzer.find_successful_edit_paths(
        llm_response_history
    )

    # AIDEV-NOTE: Stamp sequential global_edit_idx per trajectory so the
    # minimizer can track edits by index. Each trajectory starts at 0.
    for edit_path in succ_edit_paths:
        uid = 0
        for _, edits in edit_path:
            for edit in edits:
                edit.global_edit_idx = uid
                uid += 1

    return succ_edit_paths, trajectory_names


def count_trajectories(
    folder_path: str,
    bug_id: str,
    model_id: str,
    base_commit: str,
) -> int:
    """Count successful trajectories in a CrashFixer .pkl."""
    succ_edit_paths, _ = load_all_trajectories(folder_path, bug_id, model_id, base_commit)
    return len(succ_edit_paths)


def load_trajectory(
    folder_path: str,
    bug_id: str,
    model_id: str,
    base_commit: str,
    *,
    trajectory_index: int = 0,
) -> tuple[PatchList, list[str]]:
    """Load a single successful trajectory as a patch_list.

    Args:
        folder_path: Directory containing the .pkl file.
        bug_id: Kernel bug identifier.
        model_id: Model identifier.
        base_commit: Git commit SHA.
        trajectory_index: Which successful trajectory to return (0-based).

    Returns:
        (patch_list, trajectory_name) for the selected trajectory.

    Raises:
        IndexError: If trajectory_index is out of range.
    """
    succ_edit_paths, trajectory_names = load_all_trajectories(
        folder_path, bug_id, model_id, base_commit,
    )
    return succ_edit_paths[trajectory_index], trajectory_names[trajectory_index]
