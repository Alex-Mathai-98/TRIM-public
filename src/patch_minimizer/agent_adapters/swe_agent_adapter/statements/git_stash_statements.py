"""SWE-agent ``git stash`` save / restore revert statements (detection-only).

The stash stack itself lives on ``TrajectoryParser`` (``_perform_stash_save`` /
``_perform_stash_restore``); these statements only decide *whether* a step stashes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from patch_minimizer.agent_adapters.agent_adapter_base.statements import RevertStatement

if TYPE_CHECKING:
    from patch_minimizer.agent_adapters.agent_adapter_base import TrajectoryParser

_NON_SAVE_SUBCMDS = ("pop", "apply", "show", "list", "drop")


class GitStashSaveStatement(RevertStatement):
    """``git stash`` (not pop/apply/show/list/drop) that left the tree clean."""

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        if "git stash" not in action:
            return False
        return not any(f"stash {sub}" in action for sub in _NON_SAVE_SUBCMDS)

    def is_successful(self, parser: TrajectoryParser) -> bool:
        return self.step.get("state", {}).get("diff", None) == ""


class GitStashRestoreStatement(RevertStatement):
    """``git stash pop`` / ``git stash apply`` that brought edits back."""

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return "stash pop" in action or "stash apply" in action

    def is_successful(self, parser: TrajectoryParser) -> bool:
        return self.step.get("state", {}).get("diff", None) != ""
