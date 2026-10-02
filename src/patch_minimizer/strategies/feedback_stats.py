"""Feedback-stat summarization.

AIDEV-NOTE: Benchmark-agnostic — pure dict manipulation over feedback counters.
Lives in core because ``strategies/aggregate_feedback_stats.py`` depends on it;
if it lived in a frontend, core would import upward. See claude_plans/frontend-split-clean-design.md.
"""
from __future__ import annotations


def _summarize_feedback_stats(detailed: dict) -> dict:
    """Convert detailed feedback counts to summary format."""
    # AIDEV-NOTE: Pop node-tracking metadata (lists/ints) — not feedback counters
    detailed.pop("nodes_original_count", None)  # superseded by node_list_original
    _node_original = detailed.pop("node_list_original", None)
    _node_phase1 = detailed.pop("node_list_after_phase1", None)
    _node_phase2 = detailed.pop("node_list_after_phase2", None)
    _node_refine = detailed.pop("node_list_after_refinement", None)
    node_tracking = {
        "counts": {
            "original": len(_node_original or []),
            "after_phase1": len(_node_phase1 or []),
            "after_phase2": len(_node_phase2 or []),
            "after_refinement": len(_node_refine or []),
        },
        "node_list_original": _node_original,
        "node_list_after_phase1": _node_phase1,
        "node_list_after_phase2": _node_phase2,
        "node_list_after_refinement": _node_refine,
        "node_refinement_passes": detailed.pop("node_refinement_passes", 0),
    }
    # AIDEV-NOTE: Pop edit-level index tracking (lists of global_edit_idx) — not feedback counters
    _edit_original = detailed.pop("edit_list_original", None)
    _edit_after_node_min = detailed.pop("edit_list_after_node_min", None)
    _edit_file_min = detailed.pop("edit_list_after_file_min", None)
    _edit_greedy = detailed.pop("edit_list_after_greedy", None)
    _edit_refine = detailed.pop("edit_list_after_refinement", None)
    # AIDEV-NOTE: edits_after_node_min (int) is now redundant with edit_list_after_node_min (list)
    detailed.pop("edits_after_node_min", None)
    edit_tracking = {
        "counts": {
            "original": len(_edit_original or []),
            "after_node_min": len(_edit_after_node_min or []),
            "after_file_min": detailed.pop("edits_after_file_min", None),
            "after_greedy_removal": len(_edit_greedy or []),
            "after_refinement": len(_edit_refine or []),
        },
        "edit_list_original": _edit_original,
        "edit_list_after_node_min": _edit_after_node_min,
        "edit_list_after_file_min": _edit_file_min,
        "edit_list_after_greedy": _edit_greedy,
        "edit_list_after_refinement": _edit_refine,
        "edit_refinement_passes": detailed.pop("edit_refinement_passes", 0),
    }
    # AIDEV-NOTE: Separate cache_hit keys from real feedback keys
    real = {k: v for k, v in detailed.items() if not k.endswith("_cache_hit")}
    cached = {k: v for k, v in detailed.items() if k.endswith("_cache_hit")}
    total = sum(real.values())
    total_cache_hits = sum(cached.values())
    return {
        "total_feedbacks": total,
        "total_cache_hits": total_cache_hits,
        "by_algorithm": {
            "find_minimum_substring": sum(v for k, v in real.items() if k.startswith("find_minimum_substring")),
            "find_minimum_subsequence": sum(v for k, v in real.items() if k.startswith("find_minimum_subsequence")),
            "greedy_removal_node": sum(v for k, v in real.items() if k.startswith("greedy_removal") and "_node_" in k),
            "greedy_removal_file": sum(v for k, v in real.items() if k.startswith("greedy_removal") and "_file_" in k),
            "greedy_removal_edit": sum(v for k, v in real.items() if k.startswith("greedy_removal") and "_edit_" in k),
        },
        "by_granularity": {
            "node": sum(v for k, v in real.items() if "_node_" in k),
            "file": sum(v for k, v in real.items() if "_file_" in k),
            "edit": sum(v for k, v in real.items() if "_edit_" in k),
        },
        "by_outcome": {
            "no_crash": sum(v for k, v in real.items() if k.endswith("no_crash")),
            "still_crashes": sum(v for k, v in real.items() if k.endswith("still_crashes")),
            "build_failed": sum(v for k, v in real.items() if k.endswith("build_failed")),
            "not_applicable": sum(v for k, v in real.items() if k.endswith("not_applicable")),
        },
        "node_tracking": node_tracking,
        "edit_tracking": edit_tracking,
        "detailed": detailed,
    }