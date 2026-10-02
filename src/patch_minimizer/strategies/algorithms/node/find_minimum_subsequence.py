from __future__ import annotations
from copy import deepcopy
from typing import List, TYPE_CHECKING

from patch_minimizer.core.node import Node
from patch_minimizer.strategies.rule_engines import Decision

if TYPE_CHECKING:
    from patch_minimizer.strategies.algorithms.patch_candidate_tester import PatchCandidateTester


# AIDEV-NOTE: Phase 2 — greedy removal of intermediate patches (keeps first & last)
# Uses total_lines guard to prevent dropping edits that simplify the patch
def find_minimum_subsequence(bug_id, patch_tester: PatchCandidateTester, feedback_aggregator,
                             patch_range: List[Node], first_diff, rule_engine, logger) -> List[Node]:
    """Remove unnecessary intermediate patches from patch_range (keeps first & last)."""
    patch_tester._phase = "find_minimum_subsequence"
    logger.info(f"[{bug_id}] {patch_tester._granularity.capitalize()}-level: Entering minimum subsequence search")

    # Get baseline total_lines for current patch_range (no edits being dropped)
    baseline_result = patch_tester.try_candidate(first_diff, patch_range, feedback_aggregator, edits_being_dropped=None)
    assert baseline_result is not None, "Baseline patch_range must be applicable"
    feedback_aggregator.reset_baseline(baseline_result.patch_stats.total_lines)

    counter = len(patch_range) - 2

    while counter >= 1:
        candidate = deepcopy(patch_range)
        candidate[counter] = None

        # AIDEV-NOTE: The element being considered for dropping
        edits_being_dropped = patch_range[counter] if patch_range[counter] is not None else []

        result = patch_tester.try_candidate(first_diff, candidate, feedback_aggregator, edits_being_dropped)
        if result is not None:
            decision = rule_engine.decide(
                result, bug_id, patch_tester._phase, patch_tester._granularity,
                counter, has_solution=False
            )
            if decision.decision == Decision.DROP:
                patch_range[counter] = None
                feedback_aggregator.confirm_drop()

        counter -= 1

    final = [p for p in patch_range if p is not None]
    print(f"\t[{bug_id}] {patch_tester._granularity.capitalize()}-level subsequence reduction: {len(patch_range)} → {len(final)}")
    return final
