"""OpenHands ``events[]`` trajectory parser. Self-registers on import."""
from __future__ import annotations

import logging
import re
from typing import ClassVar

from patch_minimizer.core.edit import Edit
from patch_minimizer.agent_adapters.agent_adapter_base.statements import RevertStatement, Statement
from patch_minimizer.agent_adapters.agent_adapter_base import (
    CandidateTrajectory,
    Revision,
    TrajectoryParser,
    parser_registry,
)
from patch_minimizer.agent_adapters.openhands_adapter.file_editor_action_extractor import (
    FileEditorActionExtractor,
)
from patch_minimizer.agent_adapters.openhands_adapter.statements import (
    CpStatement,
    FileEditorStatement,
    GitResetAllStatement,
    GitResetFileStatement,
    OpenHandsRunStatement,
    RMStatement,
    SedStatement,
    UndoEditStatement,
)

logger = logging.getLogger(__name__)

# AIDEV-NOTE: kGym boundary regex — local to the OpenHands parser.
_RUN_KERNEL_RE = re.compile(r"\brun_kernel\b", re.IGNORECASE)


def _command(step: dict) -> str:
    """``args.command`` of an OpenHands event — the payload statements dispatch on."""
    return str((step.get("args") or {}).get("command") or "")


def _text_invokes_run_kernel(text: str) -> bool:
    if not text:
        return False
    if "/kbdr/run_kernel" in text.lower():
        return True
    return _RUN_KERNEL_RE.search(text) is not None


class KarenaOpenHandsTrajectoryParser(TrajectoryParser):
    """Parses Karena ``traj.json`` exports with top-level ``events``.

    Each ``Revision`` holds the successful ``FileEditorAction`` edits
    accumulated since the previous ``TerminalAction`` that invoked
    ``run_kernel`` (or since the start of the trace).
    """

    def __init__(self):
        from patch_minimizer.agent_adapters.agent_adapter_base import utils as _u
        _u.REPO_TREE_PREFIX = "/linux/"

    @classmethod
    def payload_keys(cls) -> tuple[str, ...]:
        return ("events",)

    # AIDEV-NOTE: ``run`` actions dispatch over RUN_STATEMENTS, first match wins
    # (sed → rm → cp, the historical if/elif order); ``edit`` actions over
    # FILE_EDITOR_STATEMENTS. ``action`` payload = ``args.command`` in both cases.
    RUN_STATEMENTS: ClassVar[tuple[type[OpenHandsRunStatement], ...]] = (
        SedStatement,
        RMStatement,
        CpStatement,
    )
    FILE_EDITOR_STATEMENTS: ClassVar[tuple[type[Statement], ...]] = (FileEditorStatement,)

    # AIDEV-NOTE: undo idioms, first match wins: ``undo_edit`` (pop last edit) before
    # per-file ``git checkout/restore`` (drop all edits; returns False to fall through).
    UNDO_STATEMENTS: ClassVar[tuple[type[RevertStatement], ...]] = (
        UndoEditStatement,
        GitResetFileStatement,
    )

    def _edit_statement_classes(self, step: dict) -> tuple[type[Statement], ...]:
        if step.get("action") == "run":
            return self.RUN_STATEMENTS
        if step.get("action") == "edit":
            return self.FILE_EDITOR_STATEMENTS
        return ()

    def _edit_statement(self, step: dict, step_idx: int | None = None) -> Statement | None:
        """Return the first matching edit statement for this event, else ``None``."""
        cmd = _command(step)
        for cls in self._edit_statement_classes(step):
            stmt = cls.from_action(cmd, step, step_idx=step_idx)
            if stmt is not None:
                return stmt
        return None

    def is_edit_step(self, step: dict) -> bool:
        """True if ``step`` is a file-editor edit or a run action with sed/rm/cp."""
        cmd = _command(step)
        return any(cls.is_edit(cmd, step) for cls in self._edit_statement_classes(step))

    def is_successful_edit_step(self, step: dict) -> bool:
        """True if the matched statement's success rule passes.

        Relies on ``_obs_map`` / ``_diff_map`` / ``_run_error_ids`` populated by
        ``store_observations``. See ``OpenHandsRunStatement`` and ``FileEditorStatement``.
        """
        stmt = self._edit_statement(step)
        return stmt is not None and stmt.is_successful(self)

    def is_undo_step(self, step: dict) -> str | None:
        """Return normalized filename if ``step`` is an ``undo_edit``, else ``None``."""
        stmt = UndoEditStatement.from_action(_command(step), step)
        return None if stmt is None else stmt.target

    def is_successful_undo_step(self, step: dict) -> bool:
        """Return ``True`` if the undo actually took effect — see ``UndoEditStatement``."""
        return UndoEditStatement(_command(step), step).is_successful(self)

    def perform_undo_step(
        self,
        step: dict,
        bucket: list[Edit],
        revisions: list[Revision],
        *,
        source_label: str,
        step_idx: int,
    ) -> bool:
        """Handle ``undo_edit`` and ``git checkout/restore`` per-file reverts."""
        cmd = _command(step)
        for cls in self.UNDO_STATEMENTS:
            stmt = cls.from_action(cmd, step)
            if stmt is not None:
                return stmt.apply(
                    bucket, revisions, parser=self, source_label=source_label, step_idx=step_idx,
                )
        return False

    def step_requests_feedback(self, step: dict) -> bool:
        """Detect ``run_kernel`` / ``/KBDr/run_kernel`` in an OpenHands ``run`` action."""
        if step.get("action") != "run":
            return False
        args = step.get("args") or {}
        cmd = args.get("command")
        return isinstance(cmd, str) and _text_invokes_run_kernel(cmd)

    def is_reset_all_step(self, step: dict) -> bool:
        """Detect ``git reset --hard`` (no file args) — see ``GitResetAllStatement``."""
        return GitResetAllStatement.from_action(_command(step), step) is not None

    def parse_edit_step(
        self, step: dict, idx: int, extractor: FileEditorActionExtractor
    ) -> list[Edit]:
        # AIDEV-NOTE: ``extractor`` is unused since the statement refactor; kept for the
        # ``TrajectoryParser.parse_edit_step`` contract.
        if not self.is_edit_step(step):
            return []
        stmt = self._edit_statement(step, step_idx=idx)
        if stmt is None or not stmt.is_successful(self):
            return []
        edits = stmt.to_edits(f"openhands step {idx}")
        if step.get("action") == "run":
            for e in edits:
                e.step_index = idx
            return edits
        if edits:
            tcid = str(step.get("id") or "")
            diff = self._diff_map.get(tcid)
            # AIDEV-NOTE: OpenHands extras.diff is per-edit (not cumulative like SWE's
            # state.diff). Each diff shows what this single edit changed, computed against
            # the file after all previous edits were applied. Stored in edit_diff (not
            # expected_diff which is for cumulative state.diff from SWE-agent).
            if diff:
                edits[-1].edit_diff = diff
        return edits

    def store_observations(
        self, events: list[dict], extractor: FileEditorActionExtractor
    ) -> None:
        """Populate ``self._obs_map`` and ``self._diff_map`` from edit observations."""
        self._obs_map = {}
        self._diff_map = {}
        # AIDEV-NOTE: Track run actions that OpenHands rejected (observation="error").
        self._run_error_ids: set[str] = set()
        for ev in events:
            if ev.get("observation") == "error":
                cause = ev.get("cause")
                if cause is not None:
                    self._run_error_ids.add(str(cause))
            result = extractor.extract_observation_from_file_editor(ev)
            if result is None:
                continue
            tcid, text, diff = result
            self._obs_map[tcid] = text
            if diff:
                self._diff_map[tcid] = diff

    def parse(
        self, data: dict, *, source_label: str
    ) -> list[CandidateTrajectory]:
        events = data.get("events") or []
        extractor = FileEditorActionExtractor()
        self.store_observations(events, extractor)
        return self.parse_trajectory(
            events, extractor, source_label=source_label
        )


parser_registry.register(KarenaOpenHandsTrajectoryParser)
