"""No-op runtime environment.

AIDEV-NOTE: Benchmark-agnostic — used by any caller running with run_jobs=False,
so it lives in core rather than in a benchmark frontend.
"""
from __future__ import annotations

from typing import List

from patch_minimizer.core.edit import Edit

from .base import BaseEnvironment
from .feedback_types import PatchFeedback, RuntimeFeedback


class DryRunEnvironment(BaseEnvironment):
    """No-op environment that always reports success. Replaces run_jobs=False."""

    def get_feedback(
        self,
        patch: str,
        edits: List[Edit] = None,
        edits_being_dropped: List[Edit] = None,
    ) -> RuntimeFeedback:
        """Return success feedback without running any jobs.

        Args:
            patch: The git diff patch string (unused)
            edits: The list of Edit objects (unused)
            edits_being_dropped: The list of Edit objects being removed (unused)

        Returns:
            RuntimeFeedback indicating NO_CRASH with N/A job_id
        """
        return RuntimeFeedback(PatchFeedback.NO_CRASH, "N/A")
