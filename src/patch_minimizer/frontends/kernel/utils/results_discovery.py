"""Utilities for discovering bugs and folder paths in a kernel results directory."""
from __future__ import annotations

from pathlib import Path
from typing import List


def get_bug_ids_from_results(results_dir: Path) -> List[str]:
    """
    Extract bug IDs from a results directory.

    Assumes the results directory contains subdirectories named after bug IDs.

    Args:
        results_dir: Path to the results directory

    Returns:
        List of bug ID strings
    """
    bug_ids = []

    # Check if we have tree-based structure (tree_0, tree_1, etc.)
    tree_dirs = sorted([d for d in results_dir.iterdir() if d.is_dir() and d.name.startswith("tree_")])

    if tree_dirs:
        # Multi-tree structure - get bugs from first tree
        first_tree = tree_dirs[0]
        for bug_dir in first_tree.iterdir():
            if bug_dir.is_dir():
                bug_ids.append(bug_dir.name)
    else:
        # Single tree structure - bugs are directly in results_dir
        for bug_dir in results_dir.iterdir():
            if bug_dir.is_dir() and not bug_dir.name.startswith('.'):
                bug_ids.append(bug_dir.name)

    return sorted(bug_ids)


def get_folder_paths_for_bug(results_dir: Path, bug_id: str, model_id: str) -> List[tuple]:
    """
    Get all folder paths and folder names for a given bug across all trees.

    Args:
        results_dir: Path to the results directory
        bug_id: The bug identifier
        model_id: The model identifier

    Returns:
        List of tuples (folder_path, folder_name) for each tree containing the bug
    """
    folder_info = []

    # AIDEV-NOTE: Bug directories are prefixed with model_id (e.g., gemini-2.5-pro__<bug_id>)
    bug_dir_name = f"{model_id}__{bug_id}"

    # Check if we have tree-based structure
    tree_dirs = sorted([d for d in results_dir.iterdir() if d.is_dir() and d.name.startswith("tree_")])

    if tree_dirs:
        # Multi-tree structure
        for tree_dir in tree_dirs:
            bug_path = tree_dir / bug_dir_name
            if bug_path.exists():
                # AIDEV-NOTE: folder_path must point to tree directory (where .pkl files are stored)
                # folder_name is for identification/logging only
                folder_path = str(tree_dir)
                folder_name = f"{tree_dir.name}/{bug_id}"
                folder_info.append((folder_path, folder_name))
    else:
        # Single tree structure
        bug_path = results_dir / bug_dir_name
        if bug_path.exists():
            # AIDEV-NOTE: folder_path must point to results_dir (where .pkl files are stored)
            folder_path = str(results_dir)
            folder_name = bug_id
            folder_info.append((folder_path, folder_name))

    return folder_info


def folder_infos_from_success_paths(success_paths_for_bug, results_dir_path, bug_id):
    """Derive (folder_path, folder_name) tuples from success_paths entries."""
    folder_infos = []
    for path in success_paths_for_bug:
        # path looks like "/hyp_.../tree_X/bug_id"
        tree_name = path.split("/")[-2]
        folder_path = str(results_dir_path / tree_name)
        folder_name = f"{tree_name}/{bug_id}"
        folder_infos.append((folder_path, folder_name))
    return folder_infos
