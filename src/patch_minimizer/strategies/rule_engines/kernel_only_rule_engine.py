"""KernelOnlyRuleEngine — decides KEEP/DROP based on runtime + diff size.

Truth Table
===========

Inputs                                                            | Outputs                | Rationale
KernelEnv Feedback | GitDiffEnv Feedback | phase  | has_solution  | Decision | stop_search |
--------------------|---------------------|--------|--------------|----------|-------------|-------------------------------------------
BUILD_FAILED        | X                   | X      | X            | KEEP     | False       | Build broke — edit is necessary
STILL_CRASHES       | X                   | X      | X            | KEEP     | False       | Bug still reproduces — edit is necessary
NO_CRASH            | SMALLER_DIFF        | X      | X            | DROP     | False       | Fix held & patch got smaller — safe to drop
NO_CRASH            | LARGER_DIFF         | phase1 | True         | KEEP     | True        | Fix held but patch grew & we have a solution — stop early
NO_CRASH            | LARGER_DIFF         | phase1 | False        | KEEP     | False       | Fix held but patch grew, no solution yet — keep searching
NO_CRASH            | LARGER_DIFF         | phase2 | X            | KEEP     | False       | Fix held but patch grew in phase2 — keep the edit
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from .base import BaseRuleEngine, Decision, DecisionResult

if TYPE_CHECKING:
    from patch_minimizer.strategies.environments import EnvironmentFeedback

from patch_minimizer.strategies.environments import PatchFeedback
from patch_minimizer.strategies.environments.git_diff_environment import DiffSizeSignal


class KernelOnlyRuleEngine(BaseRuleEngine):
    """Decides KEEP/DROP based on runtime crash feedback + diff size signal."""

    def decide(self, feedback: "EnvironmentFeedback", bug_id: str, phase: str,
               granularity: str, index: int,
               has_solution: bool = False) -> DecisionResult:
        status = feedback.feedback
        job_id = feedback.job_id
        g = granularity.capitalize()

        if status == PatchFeedback.BUILD_FAILED:
            self.logger.info(f"[{bug_id}] {g}-level: Job {job_id} ==> Build failed - keeping index {index}")
            return DecisionResult(Decision.KEEP)

        if status == PatchFeedback.STILL_CRASHES:
            self.logger.info(f"[{bug_id}] {g}-level: Job {job_id} ==> Crash persists - keeping index {index}")
            return DecisionResult(Decision.KEEP)

        # NO_CRASH — read diff size signal from environment
        # AIDEV-NOTE: diff_size_feedback is always present when GitDiffEnvironment is wired in
        diff_fb = feedback.diff_size_feedback
        candidate_total = diff_fb.patch_stats.total_lines

        if diff_fb.signal == DiffSizeSignal.SMALLER_DIFF:
            self.logger.info(f"[{bug_id}] {g}-level: Job {job_id} ==> Dropped index {index} (total={candidate_total})")
            return DecisionResult(Decision.DROP)

        # NO_CRASH but total increased (LARGER_DIFF)
        if phase == "phase1" and has_solution:
            self.logger.info(f"[{bug_id}] {g}-level: Job {job_id} ==> Stopping search (total {candidate_total} > {diff_fb.baseline_total})")
            return DecisionResult(Decision.KEEP, stop_search=True)

        self.logger.info(f"[{bug_id}] {g}-level: Job {job_id} ==> Keeping simplifying {granularity} at index {index}")
        return DecisionResult(Decision.KEEP)
