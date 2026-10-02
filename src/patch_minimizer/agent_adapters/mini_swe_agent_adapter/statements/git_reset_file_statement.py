"""mini-swe per-file ``git checkout/restore/reset --hard <files>`` revert statement."""
from __future__ import annotations

import logging
import re
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


def reset_file_succeeded(step: dict) -> bool:
    """Step-level: positive "Updated N path(s)" wins; otherwise a non-zero rc fails.

    AIDEV-NOTE: check the positive signal before rc, because compound commands
    (``git checkout f && cp …``) can have rc!=0 from a later command even though the
    checkout succeeded. "Updated 0 paths" = staged-but-not-reverted checkout.
    """
    resp = step.get("user_response", "")
    if "Updated 0 paths" in resp:
        return False
    if re.search(r"Updated \d+ paths?", resp):
        return True
    rc = step.get("returncode")
    return not (rc is not None and rc != 0)


class GitResetFileStatement(GitResetFileStatementBase):
    """Per-block file revert; drops **all** prior edits on the block's targets.

    AIDEV-NOTE: departs from SWE ``undo_edit`` semantics on purpose — multi-file, and a
    full revert (filter, not pop). ``apply`` returns ``False`` so the walk falls through to
    ``parse_edit_step``: one assistant turn can reset one file and edit another in
    different blocks, and those concurrent edits must not be lost.
    """

    @classmethod
    def matches(cls, action: str, step: dict) -> bool:
        return is_git_reset_file_command(action)

    def is_successful(self, parser: TrajectoryParser) -> bool:
        return reset_file_succeeded(self.step)

    def apply(
        self,
        bucket: list[Edit],
        revisions: list[Revision],
        *,
        parser: TrajectoryParser,
        source_label: str,
        step_idx: int,
    ) -> bool:
        if not self.targets or not self.is_successful(parser):
            return False
        drop_edits_on_files(bucket, revisions, self.targets)
        logger.debug(
            "%s step %d: git checkout reverted %s — dropped prior edits on those files",
            source_label, step_idx, sorted(self.targets),
        )
        return False
