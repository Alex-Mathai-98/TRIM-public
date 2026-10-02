from __future__ import annotations
from typing import List, TYPE_CHECKING

from patch_minimizer.core.node import Node
from patch_minimizer.strategies.rule_engines import Decision

if TYPE_CHECKING:
    from patch_minimizer.strategies.algorithms.patch_candidate_tester import PatchCandidateTester


# AIDEV-NOTE: Phase 1 — backward elimination to find suffix with minimum total_lines that fixes the bug
# Searches for local minimum: continues while total_lines decreases, stops when it increases
def find_minimum_substring(bug_id, patch_tester: PatchCandidateTester, feedback_aggregator,
                           patch_list: List[Node], first_diff, rule_engine) -> List[Node]:
    """Find contiguous suffix of patch_list that fixes the bug with minimum total_lines."""
    patch_tester._phase = "find_minimum_substring"
    index = len(patch_list) - 1
    patch_range = []
    best_patch_range = None  # Track best solution found

    # Get baseline total_lines for full patch_list (no edits being dropped)
    baseline_result = patch_tester.try_candidate(first_diff, patch_list, feedback_aggregator, edits_being_dropped=None)
    assert baseline_result is not None, "Full patch_list must be applicable"
    feedback_aggregator.reset_baseline(baseline_result.patch_stats.total_lines)

    while index >= 0:
        patch_range = [patch_list[index]] + patch_range

        # Early exit: all patches included - return best found or full list
        if len(patch_range) == len(patch_list):
            if best_patch_range is not None:
                # Found a better solution earlier, return it
                removed = len(patch_list) - len(best_patch_range)
                print(f"\t[{bug_id}] {patch_tester._granularity.capitalize()}-level minimization: {removed} {patch_tester._unit} removed ({len(patch_list)} → {len(best_patch_range)})")
                return best_patch_range
            else:
                # No smaller suffix worked, return full list (already validated at start)
                print(f"[{bug_id}] All {patch_tester._unit} are needed. No need to validate - already validated.")
                return patch_range

        # AIDEV-NOTE: Edits NOT in patch_range are being considered for dropping
        edits_being_dropped = [edit for i, patch in enumerate(patch_list)
                               if i < index for edit in patch]

        result = patch_tester.try_candidate(first_diff, patch_range, feedback_aggregator, edits_being_dropped)
        if result is not None:
            decision = rule_engine.decide(
                result, bug_id, patch_tester._phase, patch_tester._granularity,
                index, has_solution=(best_patch_range is not None)
            )
            if decision.decision == Decision.DROP:
                feedback_aggregator.confirm_drop()
                best_patch_range = list(patch_range)
                index -= 1
                continue
            elif decision.stop_search:
                break

        index -= 1

    # Return best found, or full patch_list if no valid solution found
    if best_patch_range is not None:
        removed = len(patch_list) - len(best_patch_range)
        print(f"\t[{bug_id}] {patch_tester._granularity.capitalize()}-level minimization: {removed} {patch_tester._unit} removed ({len(patch_list)} → {len(best_patch_range)})")
        return best_patch_range
    print(f"\t[{bug_id}] {patch_tester._granularity.capitalize()}-level minimization: 0 {patch_tester._unit} removed (no better solution found)")
    return patch_list
