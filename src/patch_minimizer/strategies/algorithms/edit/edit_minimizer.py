from __future__ import annotations
from typing import List

from patch_minimizer.core.edit import Edit
from patch_minimizer.strategies.algorithms.solution_minimization_base import SolutionMinimizationBase
from patch_minimizer.core.node import Node
from patch_minimizer.strategies.algorithms.node.minimize_nodes import minimize_nodes
from patch_minimizer.strategies.algorithms.edit.minimize_edits_from_nodes import minimize_edits_from_nodes


class EditMinimizer(SolutionMinimizationBase):
    """Thin orchestrator for edit-level minimization (node → file → edit)."""

    # AIDEV-NOTE: Edit-level minimization — node → file → edit hierarchy
    def find_independent_edits(self, bug_id, feedback_aggregator, patch_list, phase_2: bool = False,
                               node_refinement: bool = True, edit_refinement: bool = True,
                               save_dir: str = None, node_name: str = None, cleanup=None,
                               coupling_map=None):
        """Minimize a patch sequence: node level → file level → edit level.

        Args:
            bug_id: Kernel bug identifier
            feedback_aggregator: FeedbackReceiver that aggregates runtime + neural feedback
            patch_list: List of (parent_diff, List[Edit]) raw tuples
            phase_2: Whether to run Phase 2 (greedy intermediate removal) - only used for node-level
            save_dir: Directory to save minimized patch (optional)
            node_name: Node folder name for saving (optional)
            cleanup: InstrumentaionCleanUp object (optional)
            coupling_map: CouplingMap for this path (None if no coupling)

        Returns:
            tuple: (minimized_edit_list: List[List[Edit]], minimized_patch_diff: str|None)
        """
        # AIDEV-NOTE: Step 1 — node-level minimization
        self.patch_tester._granularity = "node"
        first_diff = patch_list[0][0] if patch_list else None
        unwrapped = [patch[1] for patch in patch_list if patch is not None]
        nodes = [Node(edits=edits, global_node_idx=i) for i, edits in enumerate(unwrapped)]

        # AIDEV-NOTE: Record true original edit list BEFORE node minimization
        self.patch_tester.feedback_stats["edit_list_original"] = [
            e.global_edit_idx for node in nodes for e in node
        ]

        node_minimized = minimize_nodes(
            bug_id, self.patch_tester, feedback_aggregator, nodes,
            first_diff, self.rule_engine, self.logger, phase_2=phase_2,
            refinement=node_refinement,
        )

        # AIDEV-NOTE: Step 2 — edit-level minimization (file → edit)
        edit_minimized, edit_count_metadata = minimize_edits_from_nodes(
            bug_id, self.patch_tester, feedback_aggregator, node_minimized,
            first_diff, self.rule_engine, self.logger, coupling_map=coupling_map,
            refinement=edit_refinement,
        )

        # AIDEV-NOTE: Store edit count metadata in feedback_stats
        self.patch_tester.feedback_stats.update(edit_count_metadata)

        # Step 3: Save and return
        minimized_patch = self._generate_minimized_diff(bug_id, first_diff, edit_minimized, cleanup)
        if save_dir is not None and node_name is not None:
            self._save_minimized_patch(bug_id, first_diff, edit_minimized, save_dir, node_name, cleanup)
        return edit_minimized, minimized_patch
