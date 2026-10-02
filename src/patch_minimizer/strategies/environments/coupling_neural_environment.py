"""Coupling-aware neural environment for deferred-decision greedy removal.

AIDEV-NOTE: Tracks visited/compulsory/deferred state for coupled edits.
State is advanced by the greedy loop (mark_visited), not by the rule engine.
"""
from __future__ import annotations

from typing import List

from patch_minimizer.core.coupling_types import CouplingMap
from patch_minimizer.core.edit import Edit

from .neural_environment import NeuralEnvironment, NeuralFeedback, NeuralSignal


class CouplingNeuralEnvironment(NeuralEnvironment):
    """Coupling-aware neural feedback for deferred-decision greedy removal.

    Tracks which coupling group members have been visited. Returns:
    - NeuralSignal.CAN_DROP   → not coupled, decide normally
    - NeuralSignal.DEFER      → coupled, partner(s) not yet visited
    - NeuralSignal.MUST_KEEP  → coupled, partner failed runtime → compulsory keep
    """

    def __init__(self, coupling_map: CouplingMap, edit_list: List[Edit]):
        self.coupling_map = coupling_map  # Uses global_edit_idx values
        # AIDEV-NOTE: Map global_edit_idx → position (for RESOLVE_GROUP — greedy loop needs positions).
        # Only contains surviving edits — dead UIDs are absent, acting as a natural filter.
        self._uid_to_pos = {e.global_edit_idx: i for i, e in enumerate(edit_list)}
        self._visited: set[int] = set()      # global_edit_idx values
        self._compulsory: set[int] = set()   # global_edit_idx values
        # AIDEV-NOTE: _deferred ⊂ _visited by construction (populated inside mark_visited)
        self._deferred: set[int] = set()     # global_edit_idx values

    def mark_visited(self, edit: Edit, runtime_can_drop: bool):
        """Called by the greedy loop after each edit is processed."""
        uid = edit.global_edit_idx
        if uid is None:
            return
        self._visited.add(uid)
        if not runtime_can_drop:
            for partner_uid in self.coupling_map.get_coupled_indices(uid):
                if partner_uid != uid:
                    self._compulsory.add(partner_uid)
        else:
            if len(self.coupling_map.get_coupled_indices(uid)) > 1:
                self._deferred.add(uid)

    def get_pre_feedback(self, edits_being_dropped: List[Edit]) -> NeuralFeedback:
        """Pre-runtime check: is this edit compulsory (coupled partner already failed)?

        AIDEV-NOTE: Called by FeedbackReceiver.get_pre_feedback() before runtime test.
        Returns NeuralFeedback with is_compulsory set — rule engine reads this to skip runtime.
        """
        for edit in edits_being_dropped:
            uid = edit.global_edit_idx
            if uid is not None and uid in self._compulsory:
                return NeuralFeedback(
                    signal=NeuralSignal.MUST_KEEP, confidence=1.0,
                    reasoning="Coupled partner is essential",
                    is_compulsory=True,
                )
        return NeuralFeedback(signal=NeuralSignal.CAN_DROP)

    def _get_deferred_partners(self, edit: Edit) -> list[int]:
        """Return POSITIONS of coupled partners that were deferred."""
        uid = edit.global_edit_idx
        if uid is None:
            return []
        coupled = self.coupling_map.get_coupled_indices(uid)
        # AIDEV-NOTE: Convert UIDs → positions for the greedy loop (RESOLVE_GROUP).
        # p in self._uid_to_pos filters out dead UIDs (edits dropped by node/file minimization).
        return [self._uid_to_pos[p] for p in coupled
                if p != uid and p in self._deferred and p in self._uid_to_pos]

    def get_feedback(
        self,
        patch: str,
        edits: List[Edit],
        edits_being_dropped: List[Edit] | None = None,
    ) -> NeuralFeedback:
        if not edits_being_dropped:
            return NeuralFeedback(signal=NeuralSignal.CAN_DROP)

        for dropped_edit in edits_being_dropped:
            uid = dropped_edit.global_edit_idx
            if uid is None:
                continue

            if uid in self._compulsory:
                return NeuralFeedback(
                    signal=NeuralSignal.MUST_KEEP, confidence=1.0,
                    reasoning="Coupled partner is essential",
                    is_compulsory=True,
                )

            coupled = self.coupling_map.get_coupled_indices(uid)
            if len(coupled) <= 1:
                continue

            # AIDEV-NOTE: Only consider surviving partners as "unvisited".
            # Dead UIDs (dropped by node/file minimization) are absent from _uid_to_pos.
            # Without this guard, dead partners would appear permanently unvisited → infinite DEFER.
            unvisited = [p for p in coupled if p != uid and p not in self._visited and p in self._uid_to_pos]
            if unvisited:
                # Partners still unvisited — defer without group resolution
                return NeuralFeedback(
                    signal=NeuralSignal.DEFER, confidence=1.0,
                    reasoning=f"Coupled partners {unvisited} not yet visited",
                )

            # AIDEV-NOTE: All coupled partners visited — check if any were deferred
            # (runtime-droppable). If so, resolve the group atomically.
            deferred_positions = self._get_deferred_partners(dropped_edit)
            if deferred_positions:
                return NeuralFeedback(
                    signal=NeuralSignal.RESOLVE_GROUP, confidence=1.0,
                    reasoning="All coupled partners visited — resolve group",
                    deferred_partner_positions=deferred_positions,
                )

        return NeuralFeedback(signal=NeuralSignal.CAN_DROP)
