from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patch_minimizer.strategies.environments import EnvironmentFeedback


class Decision(Enum):
    KEEP = "keep"
    DROP = "drop"
    DEFER = "defer"
    RESOLVE_GROUP = "resolve_group"


@dataclass
class DecisionResult:
    """
    decision: KEEP, DROP, DEFER, or RESOLVE_GROUP
    stop_search: Phase 1 only - when True, caller should break out of the search loop
    group_indices: For RESOLVE_GROUP — indices to drop together
    """
    decision: Decision
    stop_search: bool = False
    group_indices: list[int] | None = None


class BaseRuleEngine(ABC):
    """Abstract base for rule engines that decide KEEP/DROP/DEFER/RESOLVE_GROUP."""

    def __init__(self, logger):
        self.logger = logger

    def pre_decide(self, pre_feedback=None) -> DecisionResult | None:
        """Optional pre-runtime check. Return DecisionResult to skip runtime, or None to proceed."""
        return None

    @abstractmethod
    def decide(self, feedback: "EnvironmentFeedback", bug_id: str, phase: str,
               granularity: str, index: int,
               has_solution: bool = False) -> DecisionResult:
        """Decide whether to KEEP or DROP based on feedback and context."""
        pass
