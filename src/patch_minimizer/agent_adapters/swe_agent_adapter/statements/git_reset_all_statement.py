"""SWE-agent whole-tree ``git reset --hard`` revert statement (detection-only)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from patch_minimizer.agent_adapters.agent_adapter_base.statements import RevertStatement

if TYPE_CHECKING:
    from patch_minimizer.agent_adapters.agent_adapter_base import TrajectoryParser


class GitResetAllStatement(RevertStatement):
    """``git reset --hard`` that wiped every edit.

    AIDEV-NOTE: successful only when ``state.diff == ""`` confirms the repo is clean. The
    parser hook also resets ``_last_diff`` so the next edit's diff gate compares against
    clean state; ``parse_trajectory`` performs the clear. Per-file resets are handled
    earlier by ``GitResetFileStatement`` (``perform_undo_step``).
    """

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return "git reset --hard" in action

    def is_successful(self, parser: TrajectoryParser) -> bool:
        return self.step.get("state", {}).get("diff", None) == ""
