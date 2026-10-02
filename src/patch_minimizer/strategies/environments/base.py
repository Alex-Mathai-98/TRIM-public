"""Base environment abstraction for patch validation feedback.

AIDEV-NOTE: Provides abstract base class for all feedback environments (kernel runtime, neural LLM, etc.)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List

from patch_minimizer.core.edit import Edit


@dataclass
class BaseFeedback:
    """Base feedback from any environment.

    Subclasses should extend this with environment-specific fields.
    """
    pass


class BaseEnvironment(ABC):
    """Abstract base for all feedback environments.

    Subclasses implement get_feedback() to provide environment-specific
    feedback on patch candidates.
    """

    @abstractmethod
    def get_feedback(
        self,
        patch: str,
        edits: List[Edit],
        edits_being_dropped: List[Edit] = None
    ) -> BaseFeedback:
        """Get feedback for a patch.

        Args:
            patch: The git diff patch string to evaluate
            edits: The list of Edit objects in the patch
            edits_being_dropped: Optional list of Edit objects being removed during minimization

        Returns:
            BaseFeedback subclass with environment-specific feedback
        """
        pass
