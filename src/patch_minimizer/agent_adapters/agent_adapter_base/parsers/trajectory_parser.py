"""``TrajectoryParser`` ABC — contract every concrete adapter parser implements."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List

from patch_minimizer.core.edit import Edit
from patch_minimizer.agent_adapters.agent_adapter_base.edit_parsing_helpers.action_edit_extractor import (
    ActionEditExtractor,
)
from patch_minimizer.agent_adapters.agent_adapter_base.parsers.trajectory_types import (
    CandidateTrajectory,
    Revision,
)

logger = logging.getLogger(__name__)


class TrajectoryParser(ABC):
    """Parses one top-level trajectory payload into candidate trajectories."""

    @classmethod
    @abstractmethod
    def payload_keys(cls) -> tuple[str, ...]:
        """Top-level JSON keys this parser reads (for docs and default matching)."""

    @classmethod
    def can_parse(cls, data: dict) -> bool:
        """Return ``True`` if this parser accepts ``data``.

        Default: accept when any of ``payload_keys()`` is present and truthy.
        Subclasses override for more specific discrimination (e.g. version
        fields, nested schema checks).
        """
        return any(bool(data.get(key)) for key in cls.payload_keys())

    @abstractmethod
    def parse(
        self, data: dict, *, source_label: str
    ) -> List[CandidateTrajectory]:
        """Parse the full top-level dict into a list of ``CandidateTrajectory``.

        ``source_label`` is a human-readable id (path, URI, run id) used
        only in log messages.
        """

    def step_requests_feedback(self, step: dict) -> bool:
        """Return ``True`` if ``step`` is a kernel-feedback boundary.

        ``step`` is one element of whichever top-level list this parser
        consumes (a classic ``trajectory[]`` step, an OpenHands
        ``events[]`` event, etc.). Override in subclasses to split
        revisions at every kernel-feedback attempt; the default is no
        split, which matches adapters that produce a single merged
        revision (e.g. mini-swe).
        """
        return False

    def is_edit_step(self, step: dict) -> bool:
        """Return ``True`` if ``step`` looks like an edit attempt.

        Format-specific structural check — does this step carry the
        shape we'd extract edits from (e.g. a classic ``trajectory[]``
        step whose ``action`` contains the ``str_replace_editor`` CLI,
        or an OpenHands ``FileEditorAction``)? Override in subclasses;
        the default ``False`` matches adapters that don't inspect
        individual steps.
        """
        return False

    def is_successful_edit_step(self, step: dict) -> bool:
        """Return ``True`` if ``step`` is an edit that *succeeded*.

        Implementations usually look at the matching observation text
        (e.g. ``"has been edited"``) to distinguish a successful edit
        from a failed one. Default ``False`` so callers must opt in.
        """
        return False

    def parse_edit_step(
        self, step: dict, idx: int, extractor: ActionEditExtractor
    ) -> List[Edit]:
        """Return the successful edits encoded in one ``step`` (or ``[]``).

        ``step`` is one element of this parser's dispatch list; ``idx``
        is its position; ``extractor`` is the format-specific extractor
        instance the caller supplies. Override in subclasses that
        extract edits from discrete steps (classic SWE, OpenHands). The
        default is ``[]``, which matches adapters whose edits come from
        bulk text scans rather than per-step inspection (e.g. mini-swe).
        """
        return []

    def is_undo_step(self, step: dict) -> str | None:
        """Return the normalized filename if ``step`` is an undo, else ``None``.

        Default returns ``None`` (no undo support). Subclasses (e.g. classic
        SWE) override to detect ``str_replace_editor undo_edit``.
        """
        return None

    def is_successful_undo_step(self, step: dict) -> bool:
        """Return ``True`` if ``step`` is an undo that *succeeded*.

        Subclasses check the observation to distinguish a successful undo
        from a failed one (e.g. "No edit history found"). Default ``True``
        so existing callers that don't override get the pre-existing
        behaviour (assume success).
        """
        return True

    def perform_undo_step(
        self,
        step: dict,
        bucket: list[Edit],
        revisions: list[Revision],
        *,
        source_label: str,
        step_idx: int,
    ) -> bool:
        """If ``step`` is an undo, remove the affected edit(s) and return ``True``.

        Subclasses override with agent-specific undo semantics (e.g. file-level
        for SWE-agent, line-level for others). The implementation is responsible
        for its own debug logging.

        Default returns ``False`` (no undo support).
        """
        return False

    def is_reset_all_step(self, step: dict) -> bool:
        """Return ``True`` if ``step`` resets the repo to clean state.

        AIDEV-NOTE: Detects commands like ``git reset --hard`` that wipe
        all edits. Override in subclasses; default ``False``.
        """
        return False

    def is_stash_save_step(self, step: dict) -> bool:
        """Return ``True`` if ``step`` stashes edits (temporary save + clear)."""
        return False

    def is_stash_restore_step(self, step: dict) -> bool:
        """Return ``True`` if ``step`` restores stashed edits (e.g. ``git stash pop``)."""
        return False

    def _perform_stash_save(
        self,
        bucket: list[Edit],
        revisions: list[Revision],
        source_label: str,
        step_idx: int,
    ) -> tuple[list[Edit], int | None, list[Revision]]:
        """Save current parse state to the stash stack and clear.

        AIDEV-NOTE: Carry recorded ``feedback_commands`` forward across the
        clear so that agents using ``git stash`` as a discard idiom (never
        popping) don't lose the test invocations they ran pre-stash. The
        carried list is attached to the final revision at end-of-parse,
        deduped against existing per-revision feedback so stash-pop (which
        restores revisions verbatim) doesn't produce duplicates.
        """
        for rev in revisions:
            self._carried_feedback.extend(rev.feedback_commands)
        self._stash_stack.append(
            (list(bucket), list(revisions), self._last_diff)
        )
        self._last_diff = ""
        logger.debug(
            "%s step %d: stash-save — saved %d revisions + %d bucket edits",
            source_label, step_idx, len(revisions), len(bucket),
        )
        return [], None, []

    def _perform_stash_restore(
        self,
        source_label: str,
        step_idx: int,
    ) -> tuple[list[Edit], int | None, list[Revision]]:
        """Restore parse state from the stash stack."""
        if not self._stash_stack:
            return [], None, []
        bucket, revisions, self._last_diff = self._stash_stack.pop()
        bucket_start = bucket[0].step_index if bucket else None
        logger.debug(
            "%s step %d: stash-restore — restored %d revisions + %d bucket edits",
            source_label, step_idx, len(revisions), len(bucket),
        )
        return bucket, bucket_start, revisions

    def parse_trajectory(
        self, trajectory, extractor, *, source_label: str
    ) -> List[CandidateTrajectory]:
        """Walk ``trajectory`` steps, splitting into ``Revision`` nodes at
        kernel-feedback boundaries (``step_requests_feedback``) and
        extracting edits via ``parse_edit_step``.

        Subclasses override the four hooks; the loop structure is shared.

        AIDEV-NOTE: Also handles ``undo_edit`` steps — when detected,
        the most recent captured edit for that file is removed from the
        current bucket (or from the last committed revision if the bucket
        is empty). This mirrors SWE-agent's ``undo_edit`` semantics.
        """
        revisions: list[Revision] = []
        bucket: list[Edit] = []
        bucket_start: int | None = None
        bucket_end = 0
        last_step = 0
        self._stash_stack: list[tuple[list[Edit], list[Revision], str]] = []
        # AIDEV-NOTE: Accumulator for feedback_commands from revisions cleared
        # by reset-all / stash-save. Reattached to the final revision after
        # the loop (deduped against existing feedback so stash-pop, which
        # restores revisions verbatim, doesn't produce duplicates).
        self._carried_feedback: list[dict] = []

        def flush() -> None:
            nonlocal bucket, bucket_start, bucket_end
            if bucket and bucket_start is not None:
                revisions.append(
                    Revision(
                        revision_index=len(revisions),
                        start_step=bucket_start,
                        end_step=bucket_end,
                        edits=list(bucket),
                    )
                )
            bucket = []
            bucket_start = None

        for idx, step in enumerate(trajectory):
            if self.step_requests_feedback(step):
                flush()
                if revisions:
                    action = str(step.get("action") or "")
                    if action:
                        revisions[-1].feedback_commands.append(
                            {"step_index": idx, "command": action}
                        )
                continue

            if self.perform_undo_step(
                step, bucket, revisions,
                source_label=source_label, step_idx=idx,
            ):
                continue

            if self.is_stash_save_step(step):
                bucket, bucket_start, revisions = self._perform_stash_save(
                    bucket, revisions, source_label, idx,
                )
                continue

            if self.is_stash_restore_step(step):
                bucket, bucket_start, revisions = self._perform_stash_restore(
                    source_label, idx,
                )
                continue

            if self.is_reset_all_step(step):
                # AIDEV-NOTE: Carry feedback_commands forward across the
                # clear; see _perform_stash_save for rationale.
                for rev in revisions:
                    self._carried_feedback.extend(rev.feedback_commands)
                bucket.clear()
                bucket_start = None
                revisions.clear()
                logger.debug(
                    "%s step %d: reset-all — cleared all edits",
                    source_label, idx,
                )
                continue

            step_edits = self.parse_edit_step(step, idx, extractor)
            if not step_edits:
                continue
            if bucket_start is None:
                bucket_start = idx
            bucket.extend(step_edits)
            bucket_end = idx
            last_step = idx
        flush()

        # AIDEV-NOTE: Attach feedback_commands carried over from
        # ``revisions.clear()`` (reset-all / stash-save) to the final
        # revision, deduped against existing per-revision feedback. This
        # preserves the candidate-level "test commands the agent ran" union
        # across mid-trajectory tree-cleanup idioms (e.g. ``git stash`` never
        # popped, ``git reset --hard HEAD`` before re-applying the fix).
        if self._carried_feedback and revisions:
            existing = {
                ce["command"]
                for r in revisions
                for ce in r.feedback_commands
            }
            for ce in self._carried_feedback:
                if ce["command"] not in existing:
                    revisions[-1].feedback_commands.append(ce)
                    existing.add(ce["command"])

        # AIDEV-NOTE: Clean up revisions that became empty after undo_edit removals.
        revisions = [r for r in revisions if r.edits]
        for i, r in enumerate(revisions):
            r.revision_index = i

        if not revisions:
            logger.info("%s: no successful edits", source_label)
            return []
        return [
            CandidateTrajectory(
                revisions=revisions,
                success_step=last_step,
            )
        ]
