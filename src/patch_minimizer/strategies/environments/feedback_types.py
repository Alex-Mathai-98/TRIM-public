"""Shared runtime-feedback vocabulary.

AIDEV-NOTE: These types are benchmark-agnostic — both KernelEnvironment and
SWEBenchEnvironment emit RuntimeFeedback, and every rule engine branches on
PatchFeedback. They live in core (not in a frontend) so no frontend has to
import another frontend to speak the common language.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .base import BaseFeedback


class PatchFeedback(Enum):
    NO_CRASH = "no_crash"
    STILL_CRASHES = "still_crashes"
    BUILD_FAILED = "build_failed"


@dataclass
class RuntimeFeedback(BaseFeedback):
    """Runtime execution feedback from a runtime environment."""

    status: PatchFeedback
    job_id: str

    @property
    def feedback(self) -> PatchFeedback:
        """Alias for status to maintain compatibility with EnvironmentFeedback interface."""
        return self.status
