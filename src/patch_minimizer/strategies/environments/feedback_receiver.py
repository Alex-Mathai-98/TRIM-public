"""Feedback receiver that orchestrates multiple environments.

AIDEV-NOTE: Aggregates feedback from runtime (kernel) and neural (LLM) environments
into a single EnvironmentFeedback object.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from patch_minimizer.core.edit import Edit

from .base import BaseEnvironment
from .git_diff_environment import DiffSizeFeedback, GitDiffEnvironment, PatchStats
from .feedback_types import PatchFeedback, RuntimeFeedback
from .neural_environment import NeuralEnvironment, NeuralFeedback, NeuralSignal


@dataclass
class EnvironmentFeedback:
    """Aggregated feedback from all environments.

    Attributes:
        feedback: Runtime result (NO_CRASH, STILL_CRASHES, BUILD_FAILED)
        job_id: Job ID from the runtime environment
        patch_stats: Statistics about the patch (added/removed lines)
        neural_feedback: Optional LLM-based judgment on dropped edits
        build_failure_stage: compile_gate vs reproduction when feedback is BUILD_FAILED
    """
    feedback: PatchFeedback
    job_id: str
    patch_stats: PatchStats
    neural_feedback: NeuralFeedback | None = None
    diff_size_feedback: DiffSizeFeedback | None = None
    build_failure_stage: str | None = None


class FeedbackReceiver:
    """Orchestrates multiple environments and aggregates feedback.

    Invokes runtime environment for crash testing and optional neural
    environment for LLM-based judgment on whether edits should be kept.
    """

    def __init__(
        self,
        runtime_env: BaseEnvironment,
        neural_env: BaseEnvironment | None = None,
        git_diff_env: GitDiffEnvironment | None = None,
    ):
        """Initialize with required runtime and optional neural/diff environments.

        Args:
            runtime_env: Environment for runtime testing (KernelEnvironment or DryRunEnvironment)
            neural_env: Optional environment for LLM-based judgment (NeuralEnvironment)
            git_diff_env: Optional environment for diff size comparison
        """
        self.runtime_env = runtime_env
        self.neural_env = neural_env
        self.git_diff_env = git_diff_env

    def get_pre_feedback(self, edits_being_dropped: List[Edit]) -> NeuralFeedback:
        """Pre-runtime coupling check (before any runtime test).

        AIDEV-NOTE: Called by greedy loop before runtime. Returns NeuralFeedback
        with is_compulsory set if this edit must be kept.
        Returns CAN_DROP if no neural env or no coupling.
        """
        if self.neural_env is not None and hasattr(self.neural_env, "get_pre_feedback"):
            return self.neural_env.get_pre_feedback(edits_being_dropped)
        return NeuralFeedback(signal=NeuralSignal.CAN_DROP)

    def mark_visited(self, edit: Edit, runtime_can_drop: bool):
        """Advance neural environment state after greedy loop processes an edit.

        AIDEV-NOTE: Called by _unrestricted_greedy_removal after each decision.
        No-op if no neural env.
        """
        if self.neural_env is not None and hasattr(self.neural_env, "mark_visited"):
            self.neural_env.mark_visited(edit, runtime_can_drop)

    def get_feedback(
        self,
        patch: str,
        edits: List[Edit],
        edits_being_dropped: List[Edit] | None = None
    ) -> EnvironmentFeedback:
        """Get feedback from all environments.

        Args:
            patch: The candidate patch (without the dropped edits) for runtime testing
            edits: The edits in the candidate patch
            edits_being_dropped: The edits being considered for dropping (for neural judgment)

        Returns:
            EnvironmentFeedback with aggregated results from all environments
        """
        # Runtime feedback - tests the candidate patch
        runtime: RuntimeFeedback = self.runtime_env.get_feedback(patch, edits)

        # Neural feedback - judges the edits being dropped (with full context)
        neural = None
        if self.neural_env is not None:
            # AIDEV-NOTE: NeuralEnvironment.get_feedback has extended signature for edits_being_dropped
            if isinstance(self.neural_env, NeuralEnvironment):
                neural = self.neural_env.get_feedback(patch, edits, edits_being_dropped)
            else:
                # Fallback for any BaseEnvironment that doesn't support edits_being_dropped
                neural = self.neural_env.get_feedback(patch, edits)

        # AIDEV-NOTE: Patch stats come from GitDiffEnvironment (single parse point).
        # Fallback to direct parse only when git_diff_env is absent.
        diff_size = None
        if self.git_diff_env is not None:
            diff_size = self.git_diff_env.get_feedback(patch, edits, edits_being_dropped)
            patch_stats = diff_size.patch_stats
        else:
            patch_stats = PatchStats.from_diff(patch)

        return EnvironmentFeedback(
            feedback=runtime.status,
            job_id=runtime.job_id,
            patch_stats=patch_stats,
            neural_feedback=neural,
            diff_size_feedback=diff_size,
            build_failure_stage=getattr(runtime, "build_failure_stage", None),
        )

    def reset_baseline(self, total: int) -> None:
        """Delegate baseline reset to git_diff_env (no-op if absent)."""
        if self.git_diff_env is not None:
            self.git_diff_env.reset_baseline(total)

    def confirm_drop(self) -> None:
        """Delegate drop confirmation to git_diff_env (no-op if absent).

        AIDEV-NOTE: No-arg — GitDiffEnvironment uses the candidate_total
        from the most recent get_feedback() call.
        """
        if self.git_diff_env is not None:
            self.git_diff_env.confirm_drop()
