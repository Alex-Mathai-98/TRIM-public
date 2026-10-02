from __future__ import annotations
from typing import List, Union, TYPE_CHECKING

from patch_minimizer.strategies.environments import PatchFeedback
from patch_minimizer.strategies.rule_engines import Decision

if TYPE_CHECKING:
    from patch_minimizer.core.edit import Edit
    from patch_minimizer.core.node import Node
    from patch_minimizer.strategies.algorithms.patch_candidate_tester import PatchCandidateTester


# AIDEV-NOTE: Unrestricted greedy removal — single pass, algorithm-agnostic.
# Acts on decisions from rule_engine: KEEP, DROP, DEFER, RESOLVE_GROUP.
def unrestricted_greedy_removal(bug_id, patch_tester: PatchCandidateTester, feedback_aggregator,
                                patch_list: Union[List[Node], List[List[Edit]]], first_diff, rule_engine, logger):
    """Remove unnecessary patches via single reverse pass (no protected indices)."""
    patch_tester._phase = "greedy_removal"
    logger.info(f"[{bug_id}] {patch_tester._granularity.capitalize()}-level: Entering unrestricted greedy removal")

    if len(patch_list) <= 1:
        return patch_list

    # Get baseline (no edits being dropped)
    baseline_result = patch_tester.try_candidate(first_diff, patch_list, feedback_aggregator, edits_being_dropped=None)
    assert baseline_result is not None, "Baseline patch_list must be applicable"
    feedback_aggregator.reset_baseline(baseline_result.patch_stats.total_lines)

    working = list(patch_list)

    for counter in range(len(working) - 1, -1, -1):
        if working[counter] is None:
            continue

        # AIDEV-NOTE: The element being considered for dropping
        edits_being_dropped = working[counter] if isinstance(working[counter], list) else [working[counter]]

        # --- Pre-decision check (feedback aggregator checks compulsory) ---
        pre_feedback = feedback_aggregator.get_pre_feedback(edits_being_dropped)
        pre_decision = rule_engine.pre_decide(pre_feedback=pre_feedback)
        if pre_decision is not None:
            # AIDEV-NOTE: Compulsory keep — mark as visited (runtime_can_drop=False)
            edit = edits_being_dropped[0] if isinstance(edits_being_dropped, list) else edits_being_dropped
            feedback_aggregator.mark_visited(edit, runtime_can_drop=False)
            continue

        # --- Normal runtime test ---
        candidate = [p for i, p in enumerate(working) if i != counter and p is not None]

        if len(candidate) == 0:
            break

        result = patch_tester.try_candidate(first_diff, candidate, feedback_aggregator, edits_being_dropped)

        if result is None:
            continue

        decision = rule_engine.decide(
            result, bug_id, patch_tester._phase, patch_tester._granularity,
            counter, has_solution=False
        )

        # AIDEV-NOTE: Determine runtime_can_drop from runtime feedback (not from decision).
        # BUILD_FAILED / STILL_CRASHES → runtime says can't drop → mark_visited(False)
        # NO_CRASH → runtime says can drop → mark_visited(True)
        runtime_can_drop = result.feedback not in (
            PatchFeedback.BUILD_FAILED, PatchFeedback.STILL_CRASHES
        )
        edit = edits_being_dropped[0] if isinstance(edits_being_dropped, list) else edits_being_dropped
        feedback_aggregator.mark_visited(edit, runtime_can_drop)

        if decision.decision == Decision.DROP:
            working[counter] = None
            feedback_aggregator.confirm_drop()

        elif decision.decision == Decision.DEFER:
            pass  # Leave in working — will be resolved later

        elif decision.decision == Decision.RESOLVE_GROUP:
            # Test combined removal of this edit + all deferred partners
            all_drop_indices = {counter} | set(decision.group_indices)
            combined_candidate = [p for i, p in enumerate(working)
                                  if i not in all_drop_indices and p is not None]

            combined_result = patch_tester.try_candidate(
                first_diff, combined_candidate, feedback_aggregator, edits_being_dropped
            )
            if combined_result:
                group_decision = rule_engine.decide(
                    combined_result, bug_id, patch_tester._phase,
                    patch_tester._granularity, counter
                )
                if group_decision.decision == Decision.DROP:
                    for idx in all_drop_indices:
                        working[idx] = None
                    feedback_aggregator.confirm_drop()

        # KEEP: do nothing (edit stays in working)

    final = [p for p in working if p is not None]
    print(f"\t[{bug_id}] {patch_tester._granularity.capitalize()}-level unrestricted removal: {len(patch_list)} → {len(final)}")
    return final
