"""Shared ``undo_edit`` revert statement: pops the most recent edit on one file."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from patch_minimizer.agent_adapters.agent_adapter_base.statements.statement import (
    RevertStatement,
)

if TYPE_CHECKING:
    from patch_minimizer.agent_adapters.agent_adapter_base.parsers.trajectory_parser import (
        TrajectoryParser,
    )
    from patch_minimizer.agent_adapters.agent_adapter_base.parsers.trajectory_types import (
        Revision,
    )
    from patch_minimizer.core.edit import Edit

logger = logging.getLogger(__name__)


class UndoEditStatementBase(RevertStatement):
    """``undo_edit <file>`` — one undo reverses exactly one prior edit on ``target``.

    Subclasses set ``self.target`` (normalized path, or ``None`` = not an undo) in
    ``_parse`` and implement ``matches`` / ``is_successful``.
    """

    target: str | None

    @classmethod
    def from_action(
        cls, action: str, step: dict, *, step_idx: int | None = None
    ) -> UndoEditStatementBase | None:
        stmt = super().from_action(action, step, step_idx=step_idx)
        if stmt is None or stmt.target is None:
            return None
        return stmt

    def apply(
        self,
        bucket: list[Edit],
        revisions: list[Revision],
        *,
        parser: TrajectoryParser,
        source_label: str,
        step_idx: int,
    ) -> bool:
        """Pop the last edit on ``target`` (bucket first, then newest revision first).

        AIDEV-NOTE: a *failed* undo returns ``True`` (step consumed, edit kept); an undo
        with no matching prior edit returns ``False`` so the step falls through.
        """
        undo_file = self.target
        if not self.is_successful(parser):
            logger.debug(
                "%s step %d: undo_edit on %s failed, keeping edit",
                source_label, step_idx, undo_file,
            )
            return True
        for i in range(len(bucket) - 1, -1, -1):
            if bucket[i].filename == undo_file:
                bucket.pop(i)
                logger.debug(
                    "%s step %d: undo_edit removed last edit on %s",
                    source_label, step_idx, undo_file,
                )
                return True
        for rev in reversed(revisions):
            for j in range(len(rev.edits) - 1, -1, -1):
                if rev.edits[j].filename == undo_file:
                    rev.edits.pop(j)
                    logger.debug(
                        "%s step %d: undo_edit removed last edit on %s",
                        source_label, step_idx, undo_file,
                    )
                    return True
        return False
