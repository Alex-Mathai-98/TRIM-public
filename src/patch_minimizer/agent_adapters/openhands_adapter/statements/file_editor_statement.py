"""OpenHands structured ``FileEditorAction`` edit statement (str_replace / insert / create)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from patch_minimizer.agent_adapters.agent_adapter_base.statements import Statement
from patch_minimizer.agent_adapters.openhands_adapter.file_editor_action_extractor import (
    FileEditorActionExtractor,
)

if TYPE_CHECKING:
    from patch_minimizer.agent_adapters.openhands_adapter.parsers import (
        KarenaOpenHandsTrajectoryParser,
    )
    from patch_minimizer.core.edit import Edit

_EDIT_COMMANDS = ("str_replace", "insert", "create")


class FileEditorStatement(Statement):
    """``action="edit"`` event whose ``args.command`` is str_replace / insert / create.

    ``action`` here is the file-editor sub-command name, not a shell string. The event dict
    itself is ``self.step``; ``FileEditorActionExtractor`` builds the single ``Edit``.
    """

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return step.get("action") == "edit" and action in _EDIT_COMMANDS

    def is_successful(self, parser: KarenaOpenHandsTrajectoryParser) -> bool:
        """Observation says edited/created AND (for edits) an ``extras.diff`` exists.

        AIDEV-NOTE: Ground truth check — successful edits always have extras.diff.
        If the observation says success but no diff exists, the edit was a no-op.
        """
        tcid = str(self.step.get("id") or "")
        obs_text = parser._obs_map.get(tcid, "")
        if not ("has been edited" in obs_text or "File created successfully" in obs_text):
            return False
        return not ("has been edited" in obs_text and tcid not in parser._diff_map)

    def to_edits(self, explanation_prefix: str) -> list[Edit]:
        """``explanation_prefix`` is ignored — labels are ``openhands {edit|insert|create} step N``.

        AIDEV-NOTE: needs ``step_idx`` from construction (``from_action(..., step_idx=idx)``).
        Raises instead of defaulting, so a missing index can't silently label edits "step 0".
        """
        if self.step_idx is None:
            raise ValueError("FileEditorStatement.to_edits needs step_idx; pass it to from_action")
        return FileEditorActionExtractor().extract_edits(self.step, self.step_idx)
