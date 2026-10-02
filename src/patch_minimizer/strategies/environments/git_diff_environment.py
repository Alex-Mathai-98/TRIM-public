"""Git diff size environment for tracking patch size changes.

AIDEV-NOTE: Compares candidate patch total_lines against a baseline to produce
DiffSizeSignal (SMALLER_DIFF / LARGER_DIFF). Owns the baseline state so that
rule engines only read signals, not compute comparisons.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List

from unidiff import PatchSet

from patch_minimizer.core.edit import Edit

from .base import BaseFeedback


@dataclass
class PatchStats:
    """Statistics from a parsed git diff."""
    added_lines: int
    removed_lines: int
    total_lines: int  # added + removed (total lines touched)

    @staticmethod
    def from_diff(diff: str) -> "PatchStats":
        patch_set = PatchSet(diff)
        return PatchStats(
            added_lines=patch_set.added,
            removed_lines=patch_set.removed,
            total_lines=patch_set.added + patch_set.removed
        )


class DiffSizeSignal(Enum):
    """Signal indicating whether a candidate patch is smaller or larger than the baseline."""
    SMALLER_DIFF = "smaller_diff"  # candidate_total <= baseline
    LARGER_DIFF = "larger_diff"    # candidate_total > baseline


@dataclass
class DiffSizeFeedback(BaseFeedback):
    """Feedback from diff size comparison.

    Attributes:
        signal: Whether the candidate patch is smaller or larger than baseline
        patch_stats: Full PatchStats computed from the candidate patch
        baseline_total: Current baseline total lines (for logging)
    """
    signal: DiffSizeSignal
    patch_stats: PatchStats
    baseline_total: int


class GitDiffEnvironment:
    """Tracks baseline patch size and compares candidates against it.

    AIDEV-NOTE: Not a BaseEnvironment subclass — it doesn't run patches through
    runtime. It just observes PatchStats and produces DiffSizeFeedback.
    """

    def __init__(self):
        self._baseline_total: int = 0
        self._last_candidate_total: int = 0

    def reset_baseline(self, total: int) -> None:
        """Set the baseline total_lines (called at the start of each phase)."""
        self._baseline_total = total

    def confirm_drop(self) -> None:
        """Accept the last candidate as the new baseline.

        AIDEV-NOTE: No-arg — uses the candidate_total from the most recent get_feedback() call.
        Called by the greedy loop after a DROP decision.
        """
        self._baseline_total = self._last_candidate_total

    def get_feedback(
        self,
        patch: str,
        edits: List[Edit],
        edits_being_dropped: List[Edit] | None = None,
    ) -> DiffSizeFeedback:
        """Parse candidate patch and compare size against current baseline.

        AIDEV-NOTE: Single owner of PatchStats.from_diff — all patch parsing
        flows through here. FeedbackReceiver reads patch_stats from the returned
        DiffSizeFeedback instead of parsing independently.

        Args:
            patch: The candidate git diff string
            edits: Edits in the candidate patch (unused, kept for interface consistency)
            edits_being_dropped: Edits being removed (unused)

        Returns:
            DiffSizeFeedback with signal, full PatchStats, and baseline_total
        """
        patch_stats = PatchStats.from_diff(patch)
        self._last_candidate_total = patch_stats.total_lines
        signal = (
            DiffSizeSignal.SMALLER_DIFF
            if patch_stats.total_lines <= self._baseline_total
            else DiffSizeSignal.LARGER_DIFF
        )
        return DiffSizeFeedback(
            signal=signal,
            patch_stats=patch_stats,
            baseline_total=self._baseline_total,
        )
