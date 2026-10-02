"""Shared per-file ``git checkout/restore/reset --hard <files>`` revert statement."""
from __future__ import annotations

from typing import TYPE_CHECKING

from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.git_reset_file_detection import (
    extract_git_reset_file_targets,
)
from patch_minimizer.agent_adapters.agent_adapter_base.statements.statement import (
    RevertStatement,
)

if TYPE_CHECKING:
    from patch_minimizer.agent_adapters.agent_adapter_base.parsers.trajectory_types import (
        Revision,
    )
    from patch_minimizer.core.edit import Edit


def drop_edits_on_files(bucket: list[Edit], revisions: list[Revision], files: set[str]) -> None:
    """Remove **every** edit on ``files`` from ``bucket`` and all ``revisions`` (in place)."""
    bucket[:] = [e for e in bucket if e.filename not in files]
    for rev in revisions:
        rev.edits = [e for e in rev.edits if e.filename not in files]


class GitResetFileStatementBase(RevertStatement):
    """Full per-file revert: filter (not pop) all prior edits on the target files.

    ``targets`` is parsed once from ``action``. Adapters subclass to add ``matches``, the
    success rule, and their ``apply`` return value (SWE: ``True``; OpenHands/mini-swe:
    ``False`` so same-step edits still get parsed).
    """

    targets: set[str]

    def _parse(self) -> None:
        self.targets = set(extract_git_reset_file_targets(self.action))
