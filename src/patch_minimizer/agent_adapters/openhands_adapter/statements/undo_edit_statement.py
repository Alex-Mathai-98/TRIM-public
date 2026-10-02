"""OpenHands ``undo_edit`` revert statement (``action="edit"``, ``args.command="undo_edit"``)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from patch_minimizer.agent_adapters.agent_adapter_base import normalize_linux_repo_path
from patch_minimizer.agent_adapters.agent_adapter_base.statements import UndoEditStatementBase

if TYPE_CHECKING:
    from patch_minimizer.agent_adapters.openhands_adapter.parsers import (
        KarenaOpenHandsTrajectoryParser,
    )


class UndoEditStatement(UndoEditStatementBase):
    """Pops the most recent prior edit on ``args.path``."""

    def _parse(self) -> None:
        path = (self.step.get("args") or {}).get("path")
        self.target = normalize_linux_repo_path(path) if path else None

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return step.get("action") == "edit" and action == "undo_edit"

    def is_successful(self, parser: KarenaOpenHandsTrajectoryParser) -> bool:
        """Only a positive ``"undone successfully"`` observation counts."""
        obs_text = parser._obs_map.get(str(self.step.get("id") or ""), "")
        if "No edit history" in obs_text:
            return False
        if "is not an absolute path" in obs_text:
            return False
        return "undone successfully" in obs_text
