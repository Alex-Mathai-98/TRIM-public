from __future__ import annotations
from typing import List, TYPE_CHECKING

from patch_minimizer.core.node import Node
from patch_minimizer.strategies.algorithms.node.find_minimum_substring import find_minimum_substring
from patch_minimizer.strategies.algorithms.node.find_minimum_subsequence import find_minimum_subsequence
from patch_minimizer.strategies.algorithms.one_minimal_guarantee import one_minimal_guarantee

if TYPE_CHECKING:
    from patch_minimizer.strategies.algorithms.patch_candidate_tester import PatchCandidateTester


# AIDEV-NOTE: Core node-level minimization: Phase 1 (suffix) → Phase 2 (subsequence) → Refinement (1-minimal).
# Records surviving node indices in patch_tester.feedback_stats after each phase.
def minimize_nodes(bug_id, patch_tester: PatchCandidateTester, feedback_aggregator,
                   nodes: List[Node], first_diff, rule_engine, logger,
                   phase_2: bool = False, refinement: bool = True) -> List[Node]:
    """Minimize a list of Nodes through three phases.

    Args:
        bug_id: Kernel bug identifier
        patch_tester: PatchCandidateTester (must have _granularity already set to "node")
        feedback_aggregator: FeedbackReceiver for runtime + neural feedback
        nodes: List[Node] with stamped global_node_idx
        first_diff: Parent diff (may contain instrumentation)
        rule_engine: Rule engine for KEEP/DROP decisions
        logger: Logger instance
        phase_2: Whether to run Phase 2 (greedy intermediate removal)

    Returns:
        List[Node] — surviving nodes (1-minimal if >1 node)
    """
    patch_tester.feedback_stats["nodes_original_count"] = len(nodes)
    patch_tester.feedback_stats["node_list_original"] = [n.global_node_idx for n in nodes]

    print(f"[{bug_id}] Starting {patch_tester._granularity.capitalize()}-level minimization: {len(nodes)} {patch_tester._granularity}s")

    # AIDEV-NOTE: Base case - single node solution
    if len(nodes) == 1:
        print(f"[{bug_id}] Single patch solution - no {patch_tester._granularity.capitalize()}-level minimization needed")
        patch_range = nodes
        surviving = [n.global_node_idx for n in patch_range]
        patch_tester.feedback_stats["node_list_after_phase1"] = surviving
        patch_tester.feedback_stats["node_list_after_phase2"] = surviving
        patch_tester.feedback_stats["node_list_after_refinement"] = surviving
        patch_tester.feedback_stats["node_refinement_passes"] = 0
    else:
        # AIDEV-NOTE: Phase 1 — minimum contiguous substring (backward elimination)
        patch_range = find_minimum_substring(bug_id, patch_tester, feedback_aggregator, nodes, first_diff, rule_engine)
        patch_tester.feedback_stats["node_list_after_phase1"] = [n.global_node_idx for n in patch_range]

        # AIDEV-NOTE: Phase 2 — minimum subsequence (greedy intermediate removal)
        if phase_2 and len(patch_range) > 2:
            patch_range = find_minimum_subsequence(bug_id, patch_tester, feedback_aggregator, patch_range, first_diff, rule_engine, logger)
            print(f"[{bug_id}] {patch_tester._granularity.capitalize()}-level reduction: {len(nodes)} → {len(patch_range)}")
        patch_tester.feedback_stats["node_list_after_phase2"] = [n.global_node_idx for n in patch_range]

        # ==================== REFINEMENT PHASE ====================
        # AIDEV-NOTE: Repeat unrestricted greedy removal until convergence (1-minimal guarantee).
        # Theoretical O(m²), empirical O(m) — expect outer ≈ 1.
        patch_tester.feedback_stats["node_refinement_passes"] = 0
        if refinement and len(patch_range) > 1:
            patch_range, num_passes = one_minimal_guarantee(
                bug_id, patch_tester, feedback_aggregator,
                patch_range, first_diff, rule_engine, logger,
            )
            patch_tester.feedback_stats["node_refinement_passes"] = num_passes
        patch_tester.feedback_stats["node_list_after_refinement"] = [n.global_node_idx for n in patch_range]

    return patch_range
