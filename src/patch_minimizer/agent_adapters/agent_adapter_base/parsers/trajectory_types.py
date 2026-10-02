"""Core trajectory types shared by every agent adapter."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from patch_minimizer.core.edit import Edit


@dataclass
class Revision:
    """One minimization node: successful edits since the prior kernel-feedback run (or trajectory start)."""

    revision_index: int
    start_step: int
    end_step: int
    edits: list[Edit] = field(default_factory=list)
    # AIDEV-NOTE: Each entry is {"step_index": int, "command": str}.
    feedback_commands: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CandidateTrajectory:
    """One minimization input: revisions leading to the final patch."""

    revisions: list[Revision]
    success_step: int
