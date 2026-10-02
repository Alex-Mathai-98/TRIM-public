"""SWE-agent ``str_replace_editor undo_edit <file>`` revert statement."""
from __future__ import annotations

from typing import TYPE_CHECKING

from patch_minimizer.agent_adapters.agent_adapter_base import normalize_linux_repo_path
from patch_minimizer.agent_adapters.agent_adapter_base.statements import UndoEditStatementBase

if TYPE_CHECKING:
    from patch_minimizer.agent_adapters.agent_adapter_base import TrajectoryParser

_UNDO_CMD = "str_replace_editor undo_edit"


class UndoEditStatement(UndoEditStatementBase):
    """Pops the most recent prior edit on the undone file."""

    def _parse(self) -> None:
        parts = self.action.split(_UNDO_CMD, 1)[1].strip() if _UNDO_CMD in self.action else ""
        raw_path = parts.split()[0] if parts.split() else ""
        self.target = normalize_linux_repo_path(raw_path) if raw_path else None

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return _UNDO_CMD in action

    def is_successful(self, parser: TrajectoryParser) -> bool:
        """AIDEV-NOTE: SWE-agent ``undo_edit`` can fail in two ways, both leaving the file
        unchanged (so the prior edit must NOT be removed): "No edit history found", or a
        bad path ("is not an absolute path")."""
        obs_lower = str(self.step.get("observation") or "").lower()
        if "no edit history" in obs_lower:
            return False
        return "is not an absolute path" not in obs_lower
