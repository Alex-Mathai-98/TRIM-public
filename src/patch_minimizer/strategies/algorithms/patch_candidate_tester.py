from __future__ import annotations
from typing import List, TYPE_CHECKING

from patch_minimizer.core.edit import Edit
from patch_minimizer.strategies.environments import EnvironmentFeedback

if TYPE_CHECKING:
    from patch_minimizer.strategies.repo_patch_manager import RepoPatchManager


# AIDEV-NOTE: Extracted from SolutionMinimizationBase — owns the "lock → apply → diff → feedback → record" loop
class PatchCandidateTester:
    """Lock → check → apply → diff → feedback loop for testing candidate patches."""

    def __init__(self, repo: RepoPatchManager, code_dir_lock, use_result_cache: bool = True):
        self.repo = repo
        self.code_dir_lock = code_dir_lock
        self.feedback_stats: dict = {}
        self._phase: str | None = None
        self._granularity: str | None = None
        # AIDEV-NOTE: Cache keyed by frozenset of global_edit_idx — same edits = same kernel outcome
        self._use_result_cache = use_result_cache
        self._result_cache: dict[frozenset[int], EnvironmentFeedback] = {}

    def _record_feedback(self, outcome: str, from_cache: bool = False):
        if from_cache:
            cache_key = f"{self._phase}_{self._granularity}_{outcome}_cache_hit"
            self.feedback_stats[cache_key] = self.feedback_stats.get(cache_key, 0) + 1
        else:
            key = f"{self._phase}_{self._granularity}_{outcome}"
            self.feedback_stats[key] = self.feedback_stats.get(key, 0) + 1

    @property
    def _unit(self) -> str:
        """Return plural unit name based on current granularity (e.g., 'nodes', 'files', or 'edits')."""
        if self._granularity == "edit":
            return "edits"
        elif self._granularity == "file":
            return "files"
        return "nodes"

    # AIDEV-NOTE: Shared helper — lock → check → apply → diff → feedback, used by all algorithms
    def try_candidate(
        self,
        first_diff,
        patch_range,
        feedback_aggregator,
        edits_being_dropped: List[Edit] | None = None,
    ) -> EnvironmentFeedback | None:
        """Try applying a candidate patch range and get feedback.

        Args:
            first_diff: Parent diff (may contain instrumentation)
            patch_range: List of patches (List[Edit] or None entries) to apply
            feedback_aggregator: FeedbackReceiver that aggregates runtime + neural feedback
            edits_being_dropped: Edits being considered for removal (for neural feedback)

        Returns:
            EnvironmentFeedback if applicable, or None if patches can't be applied.
        """
        cache_key = None
        if self._use_result_cache:
            cache_key = frozenset(
                edit.global_edit_idx
                for patch in patch_range if patch is not None
                for edit in patch
                if edit.global_edit_idx is not None
            )
            if cache_key:
                cached = self._result_cache.get(cache_key)
                if cached is not None:
                    self._record_feedback(cached.feedback.value, from_cache=True)
                    return cached

        with self.code_dir_lock.w_locked_nowait():
            applicable = self.repo.check_patch_range(first_diff, patch_range, use_lock=False)
            if applicable:
                self.repo.check_patch_range(first_diff, patch_range, leave_patches=True, use_lock=False)
                kgym_patch = self.repo.generate_git_diff()
            else:
                kgym_patch = None
            self.repo.clean_repo(use_lock=False)

        if not applicable or kgym_patch is None:
            self._record_feedback("not_applicable")
            return None

        # AIDEV-NOTE: Flatten patch_range to get all edits in the candidate patch
        all_edits = [edit for patch in patch_range if patch is not None for edit in patch]

        result = feedback_aggregator.get_feedback(kgym_patch, all_edits, edits_being_dropped)

        if cache_key:
            self._result_cache[cache_key] = result

        self._record_feedback(result.feedback.value)
        return result
