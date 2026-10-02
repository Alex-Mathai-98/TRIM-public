"""Edit-level minimization — the benchmark-agnostic core.

AIDEV-NOTE: This module is the single place that turns a ``patch_list`` into a
minimized one. It knows nothing about any particular benchmark: callers build
their own runtime environment and minimizer and pass them in.

Why ``minimizer`` and ``feedback_aggregator`` are parameters rather than built
here: both carry state that must survive across calls.
  * the minimizer owns ``PatchCandidateTester._result_cache`` (edit-set -> feedback)
  * the aggregator owns ``GitDiffEnvironment._baseline_total`` (rolling diff baseline)
Kernel callers deliberately build them once per bug and reuse them across every
trajectory; constructing them per call would discard the cache and reset the
baseline, changing both cost and drop decisions.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List

from patch_minimizer.core.edit import Edit


# AIDEV-NOTE: Helper function to serialize Edit objects to JSON-compatible dictionaries
def serialize_edit_path(edit_path: List) -> List:
    """
    Convert a list that may contain Edit objects to JSON-serializable dictionaries.

    Handles nested structures including tuples, lists, and Edit objects.

    Args:
        edit_path: List that may contain Edit objects, tuples, or other serializable data

    Returns:
        List with Edit objects converted to dicts, preserving structure
    """
    serialized = []
    for item in edit_path:
        if isinstance(item, Edit):
            serialized.append({
                "filename": item.filename,
                "before": item.before,
                "after": item.after,
                "explanation": item.explanation
            })
        elif hasattr(item, 'edits'):
            serialized.append(serialize_edit_path(item.edits))
        elif isinstance(item, (list, tuple)):
            nested_serialized = serialize_edit_path(list(item))
            serialized.append(nested_serialized)
        else:
            # For other types, keep as-is (assuming they're JSON-serializable)
            serialized.append(item)
    return serialized


def minimize_edit_list(
    bug_id: str,
    patch_list: list,
    minimizer,
    feedback_aggregator,
    *,
    minimize_at: str = "edit",
    node_refinement: bool = True,
    edit_refinement: bool = True,
    coupling_map=None,
    save_dir: str | None = None,
    node_name: str | None = None,
    cleanup=None,
) -> dict:
    """Minimize a patch_list against an already-constructed environment.

    Args:
        bug_id: Identifier used for logging and result reporting.
        patch_list: List of ``(parent_diff, List[Edit])`` tuples. Each Edit must
            carry a unique ``global_edit_idx`` across the whole list.
        minimizer: A ``NodeMinimizer`` or ``EditMinimizer``. Owns the repo and the
            result cache; reuse it across calls to keep the cache warm.
        feedback_aggregator: A ``FeedbackReceiver`` wrapping the runtime environment.
        minimize_at: ``"node"`` or ``"edit"`` granularity.
        node_refinement: Run the node-level 1-minimal refinement pass.
        edit_refinement: Run the edit-level 1-minimal refinement pass (edit level only).
        coupling_map: Pre-computed CouplingMap (edit level only).
        save_dir: Where the minimizer should write artifacts (optional).
        node_name: Sub-directory under ``save_dir``; defaults to ``standalone__<bug_id>``.
        cleanup: Instrumentation cleanup object (optional).

    Returns:
        dict with bug_id, short_path, minimized_patch, feedback_stats,
        original_edit_count, minimized_edit_count, reduction.
    """
    assert minimize_at in ("node", "edit"), f"minimize_at must be 'node' or 'edit', got '{minimize_at}'"

    if node_name is None:
        node_name = f"standalone__{bug_id}"

    # AIDEV-NOTE: ``_save_minimized_patch`` requires ``save_dir/node_name``; CLI callers only mkdir ``save_dir``.
    if save_dir:
        os.makedirs(os.path.join(save_dir, node_name), exist_ok=True)

    minimizer.feedback_stats = {}
    minimize_fn = (
        minimizer.find_independent_nodes if minimize_at == "node"
        else minimizer.find_independent_edits
    )

    kwargs = dict(
        bug_id=bug_id,
        feedback_aggregator=feedback_aggregator,
        patch_list=patch_list,
        phase_2=True,
        node_refinement=node_refinement,
        save_dir=save_dir,
        node_name=node_name,
        cleanup=cleanup,
    )
    if minimize_at == "edit":
        kwargs["coupling_map"] = coupling_map
        kwargs["edit_refinement"] = edit_refinement

    short_path, minimized_patch = minimize_fn(**kwargs)

    if minimize_at == "edit":
        orig_count = sum(len(node[1]) for node in patch_list if node[1] is not None)
    else:
        orig_count = len(patch_list)
    min_count = len(short_path)

    return {
        "bug_id": bug_id,
        "short_path": short_path,
        "minimized_patch": minimized_patch,
        "feedback_stats": dict(minimizer.feedback_stats),
        "original_edit_count": orig_count,
        "minimized_edit_count": min_count,
        "reduction": f"{orig_count}_to_{min_count}",
    }


def save_minimization_results(result: dict, patch_list: list, save_dir: str) -> Path:
    """Write ``minimization_results.json`` for a completed minimization.

    AIDEV-NOTE: Shared by every caller on purpose — several downstream pipeline steps
    read this file, so its shape must not diverge between callers.
    """
    output_file = Path(save_dir) / "minimization_results.json"
    save_data = {
        **result,
        "short_path": serialize_edit_path(result["short_path"]),
        "original_patch_list": serialize_edit_path(patch_list),
    }
    with open(output_file, 'w') as f:
        json.dump(save_data, f, indent=2)
    print(f"Results saved to: {output_file}")
    return output_file


# AIDEV-NOTE: Atomic write helper for incremental summary updates
def _save_summary_atomic(summary_file: Path, aggregated_results: dict, successful: list,
                         failed: list, config: dict) -> None:
    """Atomic write: temp file + os.replace(). Reuses pattern from cost_tracker.py.

    AIDEV-NOTE: Benchmark-agnostic and deliberately NOT duplicated per benchmark —
    ``minimization_summary.json`` has shared readers (e.g. aggregate_feedback_stats.py,
    and the SWE-bench postprocessing pass) that parse it without knowing which benchmark
    produced it. Two writers would let the format drift silently.
    """
    # AIDEV-NOTE: successful is list of (bug_id, folder, best_reduction, stats_or_feedback) tuples
    # The 4th element is either a feedback_stats dict (from minimization) or a full
    # DD stats dict (from DD benchmark) containing tests_run, not_applicable, etc.
    successful_bugs = []
    for item in successful:
        entry = {"bug_id": item[0], "folder": item[1], "best_reduction": item[2]}
        if len(item) > 3 and item[3]:
            entry["feedback_stats"] = item[3]
        successful_bugs.append(entry)

    summary_data = {
        "aggregated_results": aggregated_results,
        "total_processed": len(successful) + len(failed),
        "successful": len(successful),
        "failed": len(failed),
        "successful_bugs": successful_bugs,
        # AIDEV-NOTE: failed items are 2-tuples (bug_id, folder) or 3-tuples (bug_id, folder, stats_dict)
        "failed_bugs": [
            {"bug_id": item[0], "folder": item[1], **({"stats": item[2]} if len(item) > 2 and item[2] else {})}
            for item in failed
        ],
        "configuration": config
    }
    temp_file = str(summary_file) + ".tmp"
    with open(temp_file, 'w') as f:
        json.dump(summary_data, f, indent=2)
    os.replace(temp_file, summary_file)
