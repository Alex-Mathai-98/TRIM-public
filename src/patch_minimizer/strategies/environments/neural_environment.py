"""Neural (LLM-based) environment for patch validation feedback.

AIDEV-NOTE: Provides LLM judgment as parallel signal to runtime crash feedback.
Override NeuralEnvironment for real implementation with actual LLM calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List

from patch_minimizer.core.edit import Edit

from .base import BaseEnvironment, BaseFeedback


class NeuralSignal(Enum):
    """Signal from neural environment to rule engine."""
    CAN_DROP = "can_drop"           # no coupling constraint — runtime decides
    MUST_KEEP = "must_keep"         # must keep (e.g., coupled partner is essential)
    DEFER = "defer"                 # defer decision (coupled partners not yet visited)
    RESOLVE_GROUP = "resolve_group" # all coupled partners visited and droppable — resolve atomically


@dataclass
class NeuralFeedback(BaseFeedback):
    """Neural environment judgment on edits being dropped.

    AIDEV-NOTE: signal replaces the old should_keep: bool | None field.
    is_compulsory and deferred_partner_positions carry coupling info so
    the rule engine doesn't need a direct neural_env reference.
    """
    signal: NeuralSignal = NeuralSignal.CAN_DROP
    confidence: float = 0.0
    reasoning: str = ""
    # AIDEV-NOTE: Coupling fields — populated by CouplingNeuralEnvironment, ignored by base impl.
    is_compulsory: bool = False
    deferred_partner_positions: list[int] = field(default_factory=list)


class NeuralEnvironment(BaseEnvironment):
    """LLM-based feedback environment.

    Provides neural judgment on whether edits being dropped are essential.
    This is a dummy implementation that returns no opinion.
    Override for real implementation with actual LLM calls.
    """

    def get_feedback(
        self,
        patch: str,
        edits: List[Edit],
        edits_being_dropped: List[Edit] | None = None
    ) -> NeuralFeedback:
        # AIDEV-NOTE: Dummy implementation - no opinion. Override for real integration.
        return NeuralFeedback()
