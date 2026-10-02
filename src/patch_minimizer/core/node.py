"""
Node dataclass for representing a group of semantic edits (SEuF).

Moved from solution_minimization/.../algorithms/node/node.py to tools/generate_patch/
to co-locate with Edit, which Node wraps.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List

from patch_minimizer.core.edit import Edit


# AIDEV-NOTE: Lightweight wrapper for a group of semantic edits (SEuF) with a stable index.
# Follows Edit.global_edit_idx pattern — ephemeral UID for tracking through minimization phases.
# Iterable — behaves like List[Edit] so try_candidate, check_patch_range, etc. work unchanged.
@dataclass
class Node:
    """A group of semantic edits (SEuF) with a stable index for tracking through minimization."""
    edits: List[Edit]
    global_node_idx: int

    def __iter__(self):
        return iter(self.edits)

    def __len__(self):
        return len(self.edits)
