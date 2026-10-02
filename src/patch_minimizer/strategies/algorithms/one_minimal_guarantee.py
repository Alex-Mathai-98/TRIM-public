from __future__ import annotations
from typing import TYPE_CHECKING

from patch_minimizer.strategies.algorithms.greedy_removal import unrestricted_greedy_removal

if TYPE_CHECKING:
    from patch_minimizer.strategies.algorithms.patch_candidate_tester import PatchCandidateTester


# AIDEV-NOTE: Fixed-point wrapper around unrestricted_greedy_removal — repeats until convergence.
# Guarantees 1-minimality: no single element can be removed without breaking the fix.
# Theoretical O(k²), empirical O(k) — expect outer ≈ 1 (nearly everything caught in first pass).
def one_minimal_guarantee(bug_id, patch_tester: PatchCandidateTester, feedback_aggregator,
                          patch_list, first_diff, rule_engine, logger):
    """Repeat unrestricted greedy removal until no more elements can be removed (1-minimal).

    Args:
        bug_id: Kernel bug identifier
        patch_tester: PatchCandidateTester for testing candidates
        feedback_aggregator: FeedbackReceiver for runtime + neural feedback
        patch_list: List of patches/edits to minimize
        first_diff: Parent diff (may contain instrumentation)
        rule_engine: Rule engine for KEEP/DROP decisions
        logger: Logger instance

    Returns:
        tuple: (surviving_patches, num_passes) where num_passes is the number of
        greedy removal loops executed (expect ≈ 1).
    """
    num_elements = len(patch_list)
    working = patch_list

    for outer in range(num_elements):
        before_length = len(working)

        working = unrestricted_greedy_removal(
            bug_id, patch_tester, feedback_aggregator,
            working, first_diff, rule_engine, logger,
        )

        after_length = len(working)

        if before_length == after_length:
            # Converged — no elements removed in this pass
            logger.info(f"[{bug_id}] {patch_tester._granularity.capitalize()}-level: "
                        f"1-minimal after {outer + 1} pass(es)")
            break

    num_passes = outer + 1
    print(f"\t[{bug_id}] {patch_tester._granularity.capitalize()}-level 1-minimal guarantee: "
          f"{num_elements} → {len(working)} ({num_passes} pass(es))")
    return working, num_passes
