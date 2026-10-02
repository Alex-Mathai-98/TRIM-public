"""
Data types for semantic edit coupling.

AIDEV-NOTE: CouplingMap is the primary output of the coupling sub-agent.
It maps edit indices to coupling groups so the minimization algorithm
can merge coupled edits into atomic units.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List


@dataclass
class CouplingGroup:
    """A group of edits that must be kept/dropped together."""

    edit_indices: FrozenSet[int]  # 0-based indices into the node's edit list
    reason: str
    coupling_type: str  # "move", "define_use", "multi_site_fix", etc.


@dataclass
class CouplingMap:
    """Maps edit indices to their coupling groups for O(1) lookup."""

    groups: List[CouplingGroup]
    _edit_to_group: Dict[int, int] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self._edit_to_group = {}
        for group_idx, group in enumerate(self.groups):
            for edit_idx in group.edit_indices:
                self._edit_to_group[edit_idx] = group_idx

    def get_coupled_indices(self, edit_index: int) -> FrozenSet[int]:
        """O(1) lookup: given an edit index, return all indices that must move with it."""
        group_idx = self._edit_to_group.get(edit_index)
        if group_idx is None:
            return frozenset({edit_index})
        return self.groups[group_idx].edit_indices

    @classmethod
    def empty(cls) -> "CouplingMap":
        """No coupling constraints."""
        return cls(groups=[])
