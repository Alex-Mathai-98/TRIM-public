"""SWE-agent per-file ``git checkout/restore/reset --hard <files>`` revert statement."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.git_reset_file_detection import (
    is_git_reset_file_command,
)
from patch_minimizer.agent_adapters.agent_adapter_base.statements import (
    GitResetFileStatementBase,
    drop_edits_on_files,
)

if TYPE_CHECKING:
    from patch_minimizer.agent_adapters.agent_adapter_base import Revision, TrajectoryParser
    from patch_minimizer.core.edit import Edit

logger = logging.getLogger(__name__)


class GitResetFileStatement(GitResetFileStatementBase):
    """Drops **all** prior edits on files whose diff block vanished from ``state.diff``."""

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return is_git_reset_file_command(action)

    @property
    def actually_reverted(self) -> set[str]:
        """Targets whose diff block is gone from this step's ``state.diff``.

        AIDEV-NOTE: ``git checkout -- <file>`` (no ``HEAD``) restores from the index, so a
        staged modification makes it a no-op and ``state.diff`` still shows the file.
        Mirrors the ``diff == ""`` safety net in ``GitResetAllStatement``.
        """
        state_diff = (self.step.get("state") or {}).get("diff", "") or ""
        return {
            f for f in self.targets
            if f"a/{f} b/{f}" not in state_diff and f"+++ b/{f}" not in state_diff
        }

    def is_successful(self, parser: TrajectoryParser) -> bool:
        return bool(self.actually_reverted)

    def apply(
        self,
        bucket: list[Edit],
        revisions: list[Revision],
        *,
        parser: TrajectoryParser,
        source_label: str,
        step_idx: int,
    ) -> bool:
        """Return ``True`` (step consumed) only if some target was really reverted.

        AIDEV-NOTE: when nothing was reverted, return ``False`` so the step falls through
        to ``parse_edit_step`` (a no-op for shell-only steps anyway).
        """
        if not self.targets:
            return False
        reverted = self.actually_reverted
        if not reverted:
            logger.debug(
                "%s step %d: git checkout/restore on %s was a no-op (state.diff still shows "
                "file) — keeping edits",
                source_label, step_idx, sorted(self.targets),
            )
            return False
        drop_edits_on_files(bucket, revisions, reverted)
        logger.debug(
            "%s step %d: git checkout/restore reverted %s — dropped prior edits on those files",
            source_label, step_idx, sorted(reverted),
        )
        return True
