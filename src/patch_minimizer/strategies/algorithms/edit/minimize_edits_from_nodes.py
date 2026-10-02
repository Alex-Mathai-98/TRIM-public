from __future__ import annotations
from typing import List, TYPE_CHECKING

from patch_minimizer.core.edit import Edit
from patch_minimizer.core.node import Node
from patch_minimizer.strategies.algorithms.edit.minimize_at_file_level import minimize_at_file_level
from patch_minimizer.strategies.algorithms.edit.get_coupling_config import get_edit_level_rule_engine_and_feedback_agg
from patch_minimizer.strategies.algorithms.greedy_removal import unrestricted_greedy_removal
from patch_minimizer.strategies.algorithms.one_minimal_guarantee import one_minimal_guarantee

if TYPE_CHECKING:
    from patch_minimizer.strategies.algorithms.patch_candidate_tester import PatchCandidateTester


# AIDEV-NOTE: Core edit-level minimization: flatten → file-level → coupling setup → edit-level greedy removal
def minimize_edits_from_nodes(bug_id, patch_tester: PatchCandidateTester, feedback_aggregator,
                              node_minimized: List[Node], first_diff, rule_engine, logger,
                              coupling_map=None, refinement: bool = True):
    """Edit-level minimization given already-minimized nodes.

    Args:
        node_minimized: List[Node] — surviving nodes from node-level minimization.

    Steps: collect edits (Node → Edit boundary) → file-level → coupling setup → edit-level greedy removal.
    Returns (edit_minimized: List[List[Edit]], feedback_stats metadata dict).
    """
    # AIDEV-NOTE: Step 1 — Node → Edit boundary: flatten Node objects into individual Edits.
    # From this point on, all data structures are List[Edit] (no more Node wrappers).
    all_edits: List[Edit] = [edit for node in node_minimized for edit in node]
    # AIDEV-NOTE: Store exact edit count after node-level minimization for minimization_summary.json
    edit_count_metadata = {"edits_after_node_min": len(all_edits)}
    # AIDEV-NOTE: This is AFTER node minimization — true original is set in edit_minimizer.py
    patch_tester.feedback_stats["edit_list_after_node_min"] = [e.global_edit_idx for e in all_edits]

    # Step 2: File-level minimization (try dropping entire files)
    file_minimized_edits = minimize_at_file_level(bug_id, patch_tester, feedback_aggregator, all_edits, first_diff, rule_engine, logger)
    patch_tester.feedback_stats["edit_list_after_file_min"] = [e.global_edit_idx for e in file_minimized_edits]

    # Step 3: Flatten to individual edits for edit-level pass
    flattened = [[edit] for edit in file_minimized_edits]
    edit_count_metadata["edits_after_file_min"] = len(flattened)

    # Step 4: Coupling-aware feedback + rule engine (no-op if no coupling)
    edit_feedback_aggregator, edit_rule_engine = get_edit_level_rule_engine_and_feedback_agg(
        feedback_aggregator, flattened, coupling_map, rule_engine, logger
    )

    # Skip if already minimal (1 edit)
    if len(flattened) <= 1:
        print(f"[{bug_id}] Only {len(flattened)} edit remaining — skipping edit-level minimization")
        # AIDEV-NOTE: Stamp greedy/refinement tracking so minimization_summary.json stays consistent
        patch_tester.feedback_stats["edit_list_after_greedy"] = [e.global_edit_idx for group in flattened for e in group]
        patch_tester.feedback_stats["edit_list_after_refinement"] = [e.global_edit_idx for group in flattened for e in group]
        return flattened, edit_count_metadata

    # Step 5: Edit-level using unrestricted algorithm (single reverse pass)
    patch_tester._granularity = "edit"
    print(f"[{bug_id}] Starting Edit-level minimization: {len(flattened)} edits")
    edit_minimized = unrestricted_greedy_removal(
        bug_id, patch_tester, edit_feedback_aggregator, flattened, first_diff, rule_engine=edit_rule_engine, logger=logger
    )
    patch_tester.feedback_stats["edit_list_after_greedy"] = [e.global_edit_idx for group in edit_minimized for e in group]

    # ==================== REFINEMENT PHASE (edit-level) ====================
    # AIDEV-NOTE: Repeat unrestricted greedy removal until convergence (1-minimal guarantee).
    # Theoretical O(w²), empirical O(w) — expect outer ≈ 1.
    if refinement and len(edit_minimized) > 1:
        edit_minimized, num_passes = one_minimal_guarantee(
            bug_id, patch_tester, edit_feedback_aggregator, edit_minimized, first_diff, rule_engine=edit_rule_engine, logger=logger
        )
        edit_count_metadata["edit_refinement_passes"] = num_passes
    patch_tester.feedback_stats["edit_list_after_refinement"] = [e.global_edit_idx for group in edit_minimized for e in group]

    return edit_minimized, edit_count_metadata
