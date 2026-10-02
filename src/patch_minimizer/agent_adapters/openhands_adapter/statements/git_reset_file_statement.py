"""OpenHands per-file ``git checkout/restore/reset --hard <files>`` revert statement."""
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
    """Drops all prior edits on the targets (no success check — OpenHands has no diff)."""

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return step.get("action") == "run" and is_git_reset_file_command(action)

    def apply(
        self,
        bucket: list[Edit],
        revisions: list[Revision],
        *,
        parser: TrajectoryParser,
        source_label: str,
        step_idx: int,
    ) -> bool:
        """AIDEV-NOTE: always returns ``False`` so the same step still reaches
        ``parse_edit_step`` (a ``run`` can revert one file and edit another)."""
        if not self.targets:
            return False
        drop_edits_on_files(bucket, revisions, self.targets)
        logger.debug(
            "%s step %d: git checkout/restore reverted %s",
            source_label, step_idx, sorted(self.targets),
        )
        return False
