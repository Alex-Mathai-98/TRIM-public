"""Classic SWE-Agent ``trajectory[]`` parser.

Self-registers with ``parser_registry`` at module bottom so ``import
swe_agent_adapter`` (which transitively imports this module via
``adapter.py``) enables dispatch on ``trajectory``-keyed payloads.
"""
from __future__ import annotations

import logging
import re
from typing import ClassVar

from patch_minimizer.core.edit import Edit, EditType
from patch_minimizer.agent_adapters.agent_adapter_base import (
    CandidateTrajectory,
    Revision,
    TrajectoryParser,
    parser_registry,
)
from patch_minimizer.agent_adapters.agent_adapter_base.statements import RevertStatement
from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.str_replace_editor_extractor import (
    StrReplaceEditorExtractor,
)
from patch_minimizer.agent_adapters.swe_agent_adapter.statements import (
    CpStatement,
    GitResetAllStatement,
    GitResetFileStatement,
    GitStashRestoreStatement,
    GitStashSaveStatement,
    RMStatement,
    SWEEditStatement,
    SedStatement,
    StrReplaceEditorStatement,
    UndoEditStatement,
)

logger = logging.getLogger(__name__)

# AIDEV-NOTE: kGym boundary regex — match actual invocations only; avoid
# scanning cumulative ``query`` fields (see the AIDEV-NOTE in
# ``step_requests_feedback``).
_RUN_KERNEL_RE = re.compile(r"\brun_kernel\b", re.IGNORECASE)


class ClassicSWEAgentTrajectoryParser(TrajectoryParser):
    """Parses upstream SWE-agent style ``trajectory[]`` (``action`` / ``observation``).

    ``Revision`` list = edits between detected ``run_kernel`` steps; no
    per-step node fallback.
    """

    def __init__(self):
        from patch_minimizer.agent_adapters.agent_adapter_base import utils as _u
        _u.REPO_TREE_PREFIX = "/linux/"

    @classmethod
    def payload_keys(cls) -> tuple[str, ...]:
        return ("trajectory",)

    def step_requests_feedback(self, step: dict) -> bool:
        """Detect kernel-feedback attempt in one classic ``trajectory[]`` step.

        AIDEV-NOTE: Only checks the step's own ``action`` field. The
        ``query`` field is the **cumulative** conversation history (grows
        by 2 messages per step). Scanning it caused every step after the
        first ``run_kernel`` to be misclassified as a kernel boundary,
        silently dropping all subsequent edits. Verified across 1602
        trajectories: no real ``run_kernel`` action is missing from the
        top-level ``action`` field.
        """
        top = step.get("action")
        if not isinstance(top, str) or not top:
            return False
        return (
            "/kbdr/run_kernel" in top.lower()
            or _RUN_KERNEL_RE.search(top) is not None
        )

    # AIDEV-NOTE: order = dispatch precedence (first match wins). Must stay
    # str_replace_editor → sed → rm → cp to reproduce the historical if/elif chain;
    # reordering changes which parser handles compound actions like ``sed … && rm …``.
    EDIT_STATEMENTS: ClassVar[tuple[type[SWEEditStatement], ...]] = (
        StrReplaceEditorStatement,
        SedStatement,
        RMStatement,
        CpStatement,
    )

    # AIDEV-NOTE: undo idioms, first match wins: ``undo_edit`` (pop last edit on one file)
    # before per-file ``git checkout/restore/reset --hard <files>`` (drop all edits on them).
    UNDO_STATEMENTS: ClassVar[tuple[type[RevertStatement], ...]] = (
        UndoEditStatement,
        GitResetFileStatement,
    )

    def _edit_statement(self, step: dict) -> SWEEditStatement | None:
        """Return the first ``EDIT_STATEMENTS`` entry matching this step's action."""
        action = str(step.get("action") or "")
        for cls in self.EDIT_STATEMENTS:
            stmt = cls.from_action(action, step)
            if stmt is not None:
                return stmt
        return None

    def is_edit_step(self, step: dict) -> bool:
        action = str(step.get("action") or "")
        return any(cls.is_edit(action, step) for cls in self.EDIT_STATEMENTS)

    def is_successful_edit_step(self, step: dict) -> bool:
        """Two gates (observation text, then ``state.diff`` changed) — see ``SWEEditStatement``."""
        stmt = self._edit_statement(step)
        return stmt is not None and stmt.is_successful(self)

    def is_reset_all_step(self, step: dict) -> bool:
        """Detect ``git reset --hard`` that wipes all edits (see ``GitResetAllStatement``).

        AIDEV-NOTE: Also resets ``_last_diff`` so the next edit's
        ``is_successful_edit_step`` compares against clean state.
        ``git stash`` is handled separately by stash save/restore hooks.
        """
        stmt = GitResetAllStatement.from_action(str(step.get("action") or ""), step)
        if stmt is None or not stmt.is_successful(self):
            return False
        self._last_diff = ""
        return True

    def is_stash_save_step(self, step: dict) -> bool:
        """Detect ``git stash`` (not pop/apply/show/list/drop) with empty diff."""
        stmt = GitStashSaveStatement.from_action(str(step.get("action") or ""), step)
        return stmt is not None and stmt.is_successful(self)

    def is_stash_restore_step(self, step: dict) -> bool:
        """Detect ``git stash pop`` or ``git stash apply`` that restores edits."""
        stmt = GitStashRestoreStatement.from_action(str(step.get("action") or ""), step)
        return stmt is not None and stmt.is_successful(self)

    def is_undo_step(self, step: dict) -> str | None:
        """Return the normalized filename if ``step`` is an ``undo_edit``, else ``None``."""
        stmt = UndoEditStatement.from_action(str(step.get("action") or ""), step)
        return None if stmt is None else stmt.target

    def is_successful_undo_step(self, step: dict) -> bool:
        """Return ``True`` if the undo actually took effect — see ``UndoEditStatement``."""
        return UndoEditStatement(str(step.get("action") or ""), step).is_successful(self)

    def perform_undo_step(
        self,
        step: dict,
        bucket: list[Edit],
        revisions: list[Revision],
        *,
        source_label: str,
        step_idx: int,
    ) -> bool:
        """Remove edits reverted by ``undo_edit`` or ``git checkout/restore <file>``.

        AIDEV-NOTE: Two undo idioms in SWE-agent trajectories:
        1. ``str_replace_editor undo_edit <file>`` — pops the most recent edit
           on that file (one undo_edit = one operation reversed).
        2. ``git checkout HEAD -- <files>`` / ``git restore <files>`` /
           ``git reset --hard <files>`` — reverts the working-tree copy back
           to HEAD/index, so **every** prior edit on those files in the same
           candidate must be dropped (filter, not pop). Mirrors the
           mini-swe-agent adapter's semantics; full-repo ``git reset --hard``
           with no file args is still handled by ``is_reset_all_step``
           because ``extract_git_reset_file_targets`` returns ``[]`` there.
        """
        action = str(step.get("action") or "")
        for cls in self.UNDO_STATEMENTS:
            stmt = cls.from_action(action, step)
            if stmt is not None:
                return stmt.apply(
                    bucket, revisions, parser=self, source_label=source_label, step_idx=step_idx,
                )
        return False

    def parse_edit_step(
        self, step: dict, idx: int, extractor: StrReplaceEditorExtractor
    ) -> list[Edit]:
        # AIDEV-NOTE: ``extractor`` is unused since the statement refactor (each statement
        # owns its parsing); kept for the ``TrajectoryParser.parse_edit_step`` contract.
        current_diff = step.get("state", {}).get("diff", None)
        stmt = self._edit_statement(step) if self.is_edit_step(step) else None
        succeeded = stmt is not None and stmt.is_successful(self)
        try:
            if not succeeded:
                return []
            edits = stmt.to_edits(f"classic_swe step {idx}")
            for e in edits:
                e.step_index = idx
            if edits and current_diff is not None:
                edits[-1].expected_diff = current_diff
            return edits
        finally:
            # AIDEV-NOTE: Always update _last_diff after a successful edit step so
            # the next step's diff gate compares against this step's state.
            if succeeded and current_diff is not None:
                self._last_diff = current_diff

    def parse(
        self, data: dict, *, source_label: str
    ) -> list[CandidateTrajectory]:
        trajectory = data.get("trajectory") or []
        # AIDEV-NOTE: Empty string = clean checkout (no diff). Not None,
        # so the first step's state.diff="" correctly matches as "no change".
        self._last_diff = ""
        extractor = StrReplaceEditorExtractor()
        return self.parse_trajectory(
            trajectory, extractor, source_label=source_label
        )


parser_registry.register(ClassicSWEAgentTrajectoryParser)
