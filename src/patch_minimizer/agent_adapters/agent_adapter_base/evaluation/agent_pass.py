"""Agent pass dataclass — one parsed trajectory with candidates and patch_lists."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from patch_minimizer.core.edit import Edit
from patch_minimizer.agent_adapters.agent_adapter_base import (
    CandidateTrajectory,
)

# patch_list format: List[(parent_diff, List[Edit])]; parent_diff is always None here
PatchList = List[Tuple[None, List[Edit]]]


@dataclass
class AgentPass:
    """One Karena run: one ``traj.json`` with parsed candidates and ``patch_lists``."""

    pass_id: str
    bug_id: str
    candidates: List[CandidateTrajectory]
    patch_lists: List[PatchList]
    run_json_path: str | None = None
    base_commit: str | None = None
    kernel_base_url: str | None = None
    # Karena ``agent_patches.agentPatchId`` when JSON comes from agent-patches export (optional).
    agent_patch_id: int | None = None

    def __post_init__(self) -> None:
        assert len(self.candidates) == len(self.patch_lists), (
            f"pass_id={self.pass_id}: candidates ({len(self.candidates)}) "
            f"!= patch_lists ({len(self.patch_lists)})"
        )

    def num_candidates(self) -> int:
        return len(self.patch_lists)

    def total_edits(self) -> int:
        return sum(
            sum(len(edits) for _, edits in pl) for pl in self.patch_lists
        )
