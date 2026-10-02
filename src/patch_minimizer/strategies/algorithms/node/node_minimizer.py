from __future__ import annotations
from typing import List

from patch_minimizer.core.edit import Edit
from patch_minimizer.strategies.algorithms.solution_minimization_base import SolutionMinimizationBase
from patch_minimizer.core.node import Node
from patch_minimizer.strategies.algorithms.node.minimize_nodes import minimize_nodes


class NodeMinimizer(SolutionMinimizationBase):
    """Thin orchestrator for node-level minimization."""

    # AIDEV-NOTE: Node-level minimization — same signature as EditMinimizer.find_independent_edits for uniform dispatch
    def find_independent_nodes(self, bug_id, feedback_aggregator, patch_list, phase_2: bool = False,
                               node_refinement: bool = True,
                               save_dir: str = None, node_name: str = None, cleanup=None,
                               granularity: str = "node"):
        """Minimize a patch sequence at node level to find the essential patches that fix a kernel bug.

        Args:
            bug_id: Kernel bug identifier
            feedback_aggregator: FeedbackReceiver that aggregates runtime + neural feedback
            patch_list: List of (parent_diff, List[Edit]) raw tuples
            phase_2: Whether to run Phase 2 (greedy intermediate removal)
            save_dir: Directory to save minimized patch (optional)
            node_name: Node folder name for saving (optional)
            cleanup: InstrumentaionCleanUp object (optional)
            granularity: Granularity label for logging (default "node")

        Returns:
            tuple: (minimized_list: List[Node], minimized_patch_diff: str|None)
        """
        self.patch_tester._granularity = granularity
        # AIDEV-NOTE: Extract first_diff and unwrap raw tuples into List[Node]
        first_diff = patch_list[0][0] if patch_list else None
        unwrapped = [patch[1] for patch in patch_list if patch is not None]
        nodes = [Node(edits=edits, global_node_idx=i) for i, edits in enumerate(unwrapped)]

        patch_range = minimize_nodes(
            bug_id, self.patch_tester, feedback_aggregator, nodes,
            first_diff, self.rule_engine, self.logger, phase_2=phase_2,
            refinement=node_refinement,
        )

        # AIDEV-NOTE: Always generate the diff; optionally save to disk
        minimized_patch = self._generate_minimized_diff(bug_id, first_diff, patch_range, cleanup)
        if save_dir is not None and node_name is not None:
            self._save_minimized_patch(bug_id, first_diff, patch_range, save_dir, node_name, cleanup)
        return patch_range, minimized_patch
